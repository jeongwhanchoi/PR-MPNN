import numpy as np
import torch
from scipy.sparse.linalg import eigs, eigsh
from torch_geometric.data import Data, Batch
from torch_geometric.utils import (
    get_laplacian,
    to_scipy_sparse_matrix,
    is_undirected,
    to_networkx
)
import networkx as nx
from GraphRicciCurvature.FormanRicci import FormanRicci
from GraphRicciCurvature.OllivierRicci import OllivierRicci


def get_second_smallest_eigval(data: Data):
    assert isinstance(data.edge_index, torch.Tensor)

    graph_is_undirected = is_undirected(data.edge_index, data.edge_weight, data.num_nodes)

    eig_fn = eigs if not graph_is_undirected else eigsh

    num_nodes = data.num_nodes
    edge_index, edge_weight = get_laplacian(
        data.edge_index,
        data.edge_weight,
        normalization='sym',
        num_nodes=num_nodes,
    )

    L = to_scipy_sparse_matrix(edge_index, edge_weight, num_nodes)

    eig_vals = eig_fn(
        L,
        k=2,
        which='SR' if not graph_is_undirected else 'SA',
        return_eigenvectors=False,
    )

    return np.real(eig_vals)[0]


def get_forman_curvature(data: nx.Graph):
    frc = FormanRicci(data)
    frc.compute_ricci_curvature()
    edges = list(data.edges)
    curvatures = [frc.G[e[0]][e[1]]['formanCurvature'] for e in edges]
    return curvatures


def get_ollivier_curvature(data: nx.Graph):
    orc = OllivierRicci(data, alpha=0.5, verbose="ERROR")
    edges = list(data.edges)
    curv_dict = orc.compute_ricci_curvature_edges(edges)
    return list(curv_dict.values())


def get_connectedness_metric(data: Batch, metric: str = 'eigval'):
    assert isinstance(data.edge_index, torch.Tensor)

    graphs = Batch.to_data_list(data)
    if metric == 'eigval':
        metrics = [get_second_smallest_eigval(g) for g in graphs]
    elif metric.lower() == 'smallest_formancurvature':
        metrics = []
        for g in graphs:
            g = to_networkx(g, to_undirected=True, remove_self_loops=True)
            cur_metric = get_forman_curvature(g)
            metrics.append(min(cur_metric))
    elif metric.lower() == 'formancurvature':
        metrics = []
        for g in graphs:
            g = to_networkx(g, to_undirected=True, remove_self_loops=True)
            cur_metric = get_forman_curvature(g)
            metrics.extend(cur_metric)
    elif metric.lower() == 'smallest_olliviercurvature':
        metrics = []
        for g in graphs:
            g = to_networkx(g, to_undirected=True, remove_self_loops=True)
            cur_metric = get_ollivier_curvature(g)
            metrics.append(min(cur_metric))
    elif metric.lower() == 'olliviercurvature':
        metrics = []
        for g in graphs:
            g = to_networkx(g, to_undirected=True, remove_self_loops=True)
            cur_metric = get_ollivier_curvature(g)
            metrics.extend(cur_metric)
    else:
        raise NotImplementedError(f"{metric} not supported")

    return metrics
