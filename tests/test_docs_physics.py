"""Numbers and formulas quoted in the guide's physics part, checked independently of Typst.

The guide computes its tables from closed forms; these tests verify the closed forms by simulation
and verify the one claim about the software (what the reported squeezing number is) against the code.
"""

from __future__ import annotations

import math

import numpy as np

from iyzee.experiment import difference_values_many

DB = 10 / math.log(10)  # dB per neper of power
EULER_GAMMA = 0.5772156649015329


def test_log_averaging_constants_quoted_in_the_physics_part() -> None:
    power = np.random.default_rng(1).exponential(1.0, 2_000_000)  # Gaussian-noise power
    db = 10 * np.log10(power)
    assert abs(db.mean() - (-EULER_GAMMA * DB)) < 0.01  # 2.51 dB low
    assert abs(db.std() - math.pi / math.sqrt(6) * DB) < 0.02  # 5.57 dB per sample


def test_log_bias_cancels_in_the_difference_and_scatter_follows_the_formula() -> None:
    rng = np.random.default_rng(2)
    n, reps, true_ratio = 1000, 2000, 0.8
    squeezed = 10 * np.log10(rng.exponential(1.0, (reps, n)) * true_ratio).mean(axis=1)
    shot = 10 * np.log10(rng.exponential(1.0, (reps, n))).mean(axis=1)
    difference = squeezed - shot
    assert abs(difference.mean() - 10 * math.log10(true_ratio)) < 0.02  # the bias is gone
    predicted = math.sqrt(2) * math.pi / math.sqrt(6) * DB / math.sqrt(n)
    assert abs(difference.std() / predicted - 1) < 0.1


def test_the_reported_number_is_the_mean_of_per_sample_db_differences() -> None:
    squeezing = np.array([[-60.0, -62.0]])
    shot = np.array([[-58.0, -58.0]])
    reported = difference_values_many(squeezing, shot, "mean")[0]
    assert reported == -3.0  # mean of (-2, -4)
    ratio_of_means = 10 * math.log10(np.mean(10 ** (squeezing / 10)) / np.mean(10 ** (shot / 10)))
    assert abs(reported - ratio_of_means) > 0.05  # not the dB ratio of average powers


def test_squeezing_parameter_and_loss_formulas_round_trip() -> None:
    r = 0.5
    assert abs(-20 * r / math.log(10) - 10 * math.log10(math.exp(-2 * r))) < 1e-12
    for eta in (0.6, 0.8, 0.9):
        v = math.exp(-2 * r)
        measured = 10 * math.log10(1 - eta + eta * v)
        recovered = (10 ** (measured / 10) - (1 - eta)) / eta
        assert abs(recovered - v) < 1e-12
        assert measured > 10 * math.log10(1 - eta)  # never below the loss floor
    # loss alone makes the anti-squeezed quadrature larger than the squeezed one
    eta = 0.8
    assert 10 * math.log10(1 - eta + eta * math.exp(2 * r)) > -10 * math.log10(
        1 - eta + eta * math.exp(-2 * r)
    )
