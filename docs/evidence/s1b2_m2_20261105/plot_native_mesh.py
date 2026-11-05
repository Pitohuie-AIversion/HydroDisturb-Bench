"""Plot three actual COMSOL native mesh snapshots (M0, M1, M2); no field interpolation.

Usage: python plot_native_mesh.py M0_NATIVE.json M1_NATIVE.json M2_NATIVE.json OUTPUT.png
"""
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
import numpy as np


def read_mesh(path):
    native = json.loads(Path(path).read_text(encoding='utf-8'))
    mesh = native['components'][0]['meshes'][0]
    assert mesh['length_unit'] == 'm'
    points = np.asarray(mesh['vertices']).T
    edges = set()
    for kind in ('tri', 'quad'):
        if kind not in mesh['elements']:
            continue
        cells = np.asarray(mesh['elements'][kind]['connectivity']).T
        for cell in cells:
            xy = points[cell]
            center = xy.mean(axis=0)
            order = np.argsort(np.arctan2(xy[:, 1] - center[1], xy[:, 0] - center[0]))
            ring = cell[order]
            for a, b in zip(ring, np.roll(ring, -1)):
                edges.add(tuple(sorted((int(a), int(b)))))
    return mesh, points[np.asarray(sorted(edges))]


if __name__ == '__main__':
    meshes = [read_mesh(sys.argv[1]), read_mesh(sys.argv[2]), read_mesh(sys.argv[3])]
    fig, axes = plt.subplots(3, 3, figsize=(18, 11), constrained_layout=True)
    views = [
        ('Full unchanged domain', (0, 0.4), (0, 0.24)),
        ('Cylinder surface and wall-adjacent layers', (0.093, 0.107), (0.113, 0.127)),
        ('Near wake: same geometric window', (0.105, 0.17), (0.105, 0.135)),
    ]
    for col, (mesh, edges) in enumerate(meshes):
        name = f"M{col}"
        for row, (label, xlim, ylim) in enumerate(views):
            ax = axes[row, col]
            mask = ((edges[:, :, 0].max(axis=1) >= xlim[0]) & (edges[:, :, 0].min(axis=1) <= xlim[1])
                    & (edges[:, :, 1].max(axis=1) >= ylim[0]) & (edges[:, :, 1].min(axis=1) <= ylim[1]))
            ax.add_collection(LineCollection(edges[mask], colors='#213b54', linewidths=0.3 if row else 0.16))
            ax.set(xlim=xlim, ylim=ylim, xlabel='x [m]', ylabel='y [m]')
            ax.set_aspect('equal')
            ax.tick_params(labelsize=8)
            ax.set_title(f"{name}: {mesh['num_elem']:,} elements | {label}", fontsize=10)
    fig.suptitle('M0 / M1 / M2 actual COMSOL solver meshes\nEdges reconstructed from archived native vertices and element connectivity', fontsize=14)
    fig.savefig(sys.argv[4], dpi=220, facecolor='white')
    plt.close(fig)
    print('Native mesh comparison figure saved')
