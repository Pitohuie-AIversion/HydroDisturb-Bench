"""Unit and integration tests for C04 World Model Data Windows and Baseline Forecasting."""

from pathlib import Path

import numpy as np
import pytest

from hydro_disturb_bench.c04_forecasting import (
    c04_forecast_from_history,
    evaluate_c04_dataset_baselines,
    score_c04_forecast,
)
from hydro_disturb_bench.c04_windows import (
    C04WindowIndex,
    C04WindowSpec,
)
from hydro_disturb_bench.comsol_java.errors import InvalidParameterError, ModelValidationError
from hydro_disturb_bench.sampling_3d import (
    create_c04_2d_slice_grid,
    save_c04_dataset,
)


def test_c04_window_spec_validation():
    """Verify C04WindowSpec validates input parameters."""
    spec = C04WindowSpec(history_frames=2, forecast_frames=2, stride=1)
    assert spec.window_length == 4

    with pytest.raises(InvalidParameterError):
        C04WindowSpec(history_frames=0, forecast_frames=1)

    with pytest.raises(InvalidParameterError):
        C04WindowSpec(history_frames=2, forecast_frames=-1)

    with pytest.raises(InvalidParameterError):
        C04WindowSpec(history_frames=2, forecast_frames=2, stride=0)


def test_c04_window_index_generation(tmp_path):
    """Verify C04WindowIndex correctly slices sliding windows."""
    grid = create_c04_2d_slice_grid(D=0.30)
    times = np.array([0.0, 0.1, 0.2, 0.3, 0.4, 0.5], dtype=np.float32)
    fields = np.ones((6, 4, 96, 192), dtype=np.float32)

    ds_path = tmp_path / "test_seq.npz"
    save_c04_dataset(ds_path, fields, grid, times, allow_overwrite=True)

    spec = C04WindowSpec(history_frames=2, forecast_frames=2, stride=1)
    index = C04WindowIndex(spec)
    entries = index.add_dataset(ds_path)

    # Total frames 6, window length 4, stride 1 -> 3 windows (starts at 0, 1, 2)
    assert len(entries) == 3
    assert len(index) == 3

    assert entries[0].start_index == 0
    assert entries[0].history_stop == 2
    assert entries[0].future_stop == 4
    assert np.allclose(entries[0].history_times, [0.0, 0.1])
    assert np.allclose(entries[0].future_times, [0.2, 0.3])

    assert entries[1].start_index == 1
    assert np.allclose(entries[1].history_times, [0.1, 0.2])
    assert np.allclose(entries[1].future_times, [0.3, 0.4])


def test_c04_window_index_rejects_insufficient_frames(tmp_path):
    """Verify C04WindowIndex rejects datasets shorter than window length."""
    grid = create_c04_2d_slice_grid(D=0.30)
    times = np.array([0.0, 0.1], dtype=np.float32)
    fields = np.ones((2, 4, 96, 192), dtype=np.float32)

    ds_path = tmp_path / "short_seq.npz"
    save_c04_dataset(ds_path, fields, grid, times, allow_overwrite=True)

    spec = C04WindowSpec(history_frames=2, forecast_frames=2)
    index = C04WindowIndex(spec)

    with pytest.raises(ModelValidationError, match="insufficient for window length"):
        index.add_dataset(ds_path)


def test_c04_window_reader(tmp_path):
    """Verify reading a window produces exact shapes and metadata."""
    grid = create_c04_2d_slice_grid(D=0.30)
    times = np.array([0.0, 0.05, 0.10, 0.15], dtype=np.float32)
    fields = np.zeros((4, 4, 96, 192), dtype=np.float32)
    for i in range(4):
        fields[i, 0] = i * 2.0  # u velocity varies linearly with frame

    ds_path = tmp_path / "seq.npz"
    save_c04_dataset(ds_path, fields, grid, times, allow_overwrite=True)

    spec = C04WindowSpec(history_frames=2, forecast_frames=1, stride=1)
    index = C04WindowIndex(spec)
    index.add_dataset(ds_path)

    w0 = index[0]
    assert w0["history"].shape == (2, 4, 96, 192)
    assert w0["future"].shape == (1, 4, 96, 192)
    assert np.allclose(w0["history_times"], [0.0, 0.05])
    assert np.allclose(w0["future_times"], [0.10])
    assert w0["solid_mask"].shape == (96, 192)
    assert w0["sdf"].shape == (96, 192)
    assert w0["channels"] == ["u", "v", "w", "p"]


def test_c04_persistence_forecast():
    """Verify persistence forecast repeats the final history state."""
    # 2D slice shape (2, 4, 3, 3)
    history = np.zeros((2, 4, 3, 3), dtype=np.float32)
    history[0, :] = 1.0
    history[1, :] = 5.0

    h_times = np.array([0.0, 1.0])
    f_times = np.array([1.5, 2.0, 3.0])

    pred = c04_forecast_from_history(history, h_times, f_times, method="persistence")
    assert pred.shape == (3, 4, 3, 3)
    assert np.allclose(pred, 5.0)


def test_c04_linear_extrapolation_forecast():
    """Verify linear extrapolation computes exact slope and leads."""
    # Linear ramp: state = 2.0 + 3.0 * t
    h_times = np.array([0.0, 1.0])
    f_times = np.array([2.0, 4.0])

    history = np.zeros((2, 4, 2, 2), dtype=np.float32)
    history[0] = 2.0  # at t = 0
    history[1] = 5.0  # at t = 1 (slope = 3.0)

    pred = c04_forecast_from_history(history, h_times, f_times, method="last_two_linear")
    assert pred.shape == (2, 4, 2, 2)
    # at t = 2: 5 + 3 * (2 - 1) = 8.0
    assert np.allclose(pred[0], 8.0)
    # at t = 4: 5 + 3 * (4 - 1) = 14.0
    assert np.allclose(pred[1], 14.0)


def test_c04_score_forecast_fluid_masking():
    """Verify forecast scoring ignores points inside solid mask."""
    # 1 lead, 4 channels, 2x2 grid
    prediction = np.zeros((1, 4, 2, 2), dtype=np.float32)
    target = np.ones((1, 4, 2, 2), dtype=np.float32)

    # Set solid mask: 1 solid point out of 4
    solid_mask = np.array([[False, False], [False, True]])

    # Fluid points = 3. Target is 1, pred is 0.
    # SAE per lead = 3 * 1.0 = 3.0
    # MAE = 3.0 / 3 = 1.0
    scores = score_c04_forecast(prediction, target, solid_mask=solid_mask)
    assert scores["fluid_points"] == 3
    assert np.allclose(scores["channels"]["u"]["mae_per_lead"], [1.0])
    assert np.allclose(scores["channels"]["u"]["rmse_per_lead"], [1.0])


@pytest.mark.artifact
def test_c04_real_slice_dataset_baseline_evaluation():
    """Verify baseline evaluation on real solved 4-frame C04 slice sequence."""
    slice_path = Path("runs/c04_s1_slice_sequence.npz")
    if not slice_path.is_file():
        pytest.skip("C04 slice sequence artifact not found")

    spec = C04WindowSpec(history_frames=2, forecast_frames=1, stride=1)
    index = C04WindowIndex(spec)
    index.add_dataset(slice_path)
    assert len(index) == 2

    report_p = evaluate_c04_dataset_baselines(index, method="persistence")
    assert report_p["total_windows"] == 2
    assert "u" in report_p["channels"]
    assert report_p["channels"]["u"]["overall_mae"] < 0.05  # reasonable physical error

    report_l = evaluate_c04_dataset_baselines(index, method="last_two_linear")
    assert report_l["total_windows"] == 2
    assert "p" in report_l["channels"]


@pytest.mark.artifact
def test_c04_real_volume_dataset_baseline_evaluation():
    """Verify baseline evaluation on real solved 4-frame C04 3D volume sequence."""
    vol_path = Path("runs/c04_s1_volume_sequence.npz")
    if not vol_path.is_file():
        pytest.skip("C04 volume sequence artifact not found")

    spec = C04WindowSpec(history_frames=2, forecast_frames=1, stride=1)
    index = C04WindowIndex(spec)
    index.add_dataset(vol_path)
    assert len(index) == 2

    report_p = evaluate_c04_dataset_baselines(index, method="persistence")
    assert report_p["total_windows"] == 2
    assert "u" in report_p["channels"]
    assert report_p["channels"]["u"]["overall_mae"] < 0.05
