#!/usr/bin/env python

# Copyright 2019 D-Wave Systems Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import print_function

import sys
import math
import random

import numpy as np

import neal
import dimod
import hybrid


# load a problem
problem = sys.argv[1]
with open(problem) as fp:
    bqm = dimod.BinaryQuadraticModel.from_coo(fp)

n = len(bqm)
m = len(bqm.quadratic)
d = 200.0 * m / n / (n - 1)
print("BQM: {} nodes, {} edges, {:.2f}% density".format(n, m, d))


# PT workflow, one approach: store temperatures/betas in states

class FixedTemperatureSampler(hybrid.Runnable, hybrid.traits.SISO):
    """PT propagate/update step.

    On each call, run fixed temperature (~`1/beta.state`) simulated annealing
    for `num_sweeps` (seeded by input sample(s)), effectively producing a new
    state by sampling from a Boltzmann distribution at the given temperature.
    """

    def __init__(self, num_sweeps=10000, **runopts):
        super(FixedTemperatureSampler, self).__init__(**runopts)
        self.num_sweeps = num_sweeps

    def next(self, state, **runopts):
        new_samples = neal.SimulatedAnnealingSampler().sample(
            state.problem, initial_states=state.samples,
            beta_range=(state.beta, state.beta), beta_schedule_type='linear',
            num_sweeps=self.num_sweeps).aggregate()

        return state.updated(samples=new_samples)


class SwapReplicas(hybrid.Runnable, hybrid.traits.MIMO):
    """PT swap replicas step.

    On each call, choose a random input state (replica), and probabilistically
    accept a swap with the adjacent state (replica).
    """

    def swap_pair(self, states, i, j):
        """One pair of states' (i, j) samples probabilistic swap."""

        s_i, s_j = states[i], states[j]
        beta_diff = s_i.beta - s_j.beta
        energy_diff = s_i.samples.first.energy - s_j.samples.first.energy

        # since `min(1, math.exp(beta_diff * energy_diff))` can overflow,
        # we need to move `min` under `exp`
        w = math.exp(min(0, beta_diff * energy_diff))
        p = random.uniform(0, 1)
        if w > p:
            # swap samples for replicas i and j
            states[i].samples, states[j].samples = s_j.samples, s_i.samples

        return states

    def next(self, states, **runopts):
        i = random.choice(range(len(states) - 1))
        j = i + 1

        return self.swap_pair(states, i, j)


n_sweeps = 10000
n_replicas = 10
n_random_swaps = n_replicas - 1
n_iterations = 10

# states are randomly initialized
state = hybrid.State.from_problem(bqm)

# get a reasonable beta range
beta_hot, beta_cold = neal.default_beta_range(bqm)

# generate betas for all branches/replicas
betas = np.geomspace(beta_hot, beta_cold, n_replicas)

# create n_replicas with geometric distribution of betas (inverse temperature)
replicas = hybrid.States(*[state.updated(beta=b) for b in betas])

# run replicas update/swap for n_iterations
# (after each update/sampling step, do n_replicas-1 swap operations)
update = hybrid.Map(FixedTemperatureSampler(num_sweeps=n_sweeps))
swap = hybrid.Loop(SwapReplicas(), max_iter=n_random_swaps)
workflow = hybrid.Loop(update | swap, max_iter=n_iterations) \
         | hybrid.MergeSamples(aggregate=True)

solution = workflow.run(replicas).result()

# show execution profile
hybrid.profiling.print_counters(workflow)

# show results
print("Solution: sample={0.samples.first}, energy={0.samples.first.energy}".format(solution))
