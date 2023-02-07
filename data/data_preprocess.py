import random
from math import ceil
from typing import Optional

import numpy as np
import torch
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import shortest_path
from torch_geometric.data import Data
from torch_geometric.data.collate import collate
from torch_geometric.utils import is_undirected, to_undirected, add_remaining_self_loops, \
    coalesce, subgraph as pyg_subgraph

from subgraph.greedy_expand import greedy_grow_tree
from subgraph.khop_subgraph import parallel_khop_neighbor


class GraphModification:
    """
    Base class, augmenting each graph with some features
    """

    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs) -> Optional[Data]:
        return None


class GraphAddRemainSelfLoop(GraphModification):
    def __call__(self, graph: Data):
        edge_index, edge_attr = add_remaining_self_loops(graph.edge_index, graph.edge_attr, num_nodes=graph.num_nodes)
        graph.edge_index = edge_index
        if graph.edge_attr is not None:
            graph.edge_attr = edge_attr
        return graph


class GraphAttrToOneHot(GraphModification):
    def __init__(self, num_node_classes, num_edge_classes):
        super(GraphAttrToOneHot, self).__init__()
        self.num_node_classes = num_node_classes
        self.num_edge_classes = num_edge_classes

    def __call__(self, graph: Data):
        assert graph.x.dtype == torch.long
        assert graph.edge_attr.dtype == torch.long

        graph.x = torch.nn.functional.one_hot(graph.x.squeeze(), self.num_node_classes).to(torch.float)
        graph.edge_attr = torch.nn.functional.one_hot(graph.edge_attr.squeeze(), self.num_edge_classes).to(torch.float)

        return graph


class GraphExpandDim(GraphModification):
    def __call__(self, graph: Data):
        if graph.y.ndim == 1:
            graph.y = graph.y[None]
        if graph.edge_attr is not None and graph.edge_attr.ndim == 1:
            graph.edge_attr = graph.edge_attr[:, None]
        return graph


class GraphToUndirected(GraphModification):
    """
    Wrapper of to_undirected:
    https://pytorch-geometric.readthedocs.io/en/latest/modules/utils.html?highlight=undirected#torch_geometric.utils.to_undirected
    """

    def __call__(self, graph: Data):
        if not is_undirected(graph.edge_index, graph.edge_attr, graph.num_nodes):
            if graph.edge_attr is not None:
                edge_index, edge_attr = to_undirected(graph.edge_index,
                                                      graph.edge_attr,
                                                      graph.num_nodes)
            else:
                edge_index = to_undirected(graph.edge_index,
                                           graph.edge_attr,
                                           graph.num_nodes)
                edge_attr = None
        else:
            if graph.edge_attr is not None:
                edge_index, edge_attr = coalesce(graph.edge_index,
                                                 graph.edge_attr,
                                                 graph.num_nodes)
            else:
                edge_index = coalesce(graph.edge_index,
                                      graph.edge_attr,
                                      graph.num_nodes)
                edge_attr = None
        new_data = Data(x=graph.x,
                        edge_index=edge_index,
                        edge_attr=edge_attr,
                        y=graph.y,
                        num_nodes=graph.num_nodes)
        for k, v in graph:
            if k not in ['x', 'edge_index', 'edge_attr', 'pos', 'num_nodes', 'batch',
                         'z', 'rd', 'node_type']:
                new_data[k] = v
        return new_data


class AugmentwithNNodes(GraphModification):

    def __call__(self, graph: Data):
        graph.nnodes = torch.tensor([graph.num_nodes])
        return graph


class AugmentWithRandomKNeighbors(GraphModification):
    """
    Sample best k neighbors randomly, return the induced subgraph
    Serves as transform because of its randomness
    """

    def __init__(self, sample_k: int, ensemble: int):
        super(AugmentWithRandomKNeighbors, self).__init__()
        self.num_neighnors = sample_k
        self.ensemble = ensemble

    def __call__(self, graph: Data):
        mask = greedy_grow_tree(graph,
                                self.num_neighnors,
                                torch.rand(graph.num_nodes, graph.num_nodes, self.ensemble, device=graph.x.device),
                                target_dtype=torch.bool)
        graph.node_mask = mask.reshape(graph.num_nodes ** 2, self.ensemble)
        return graph


class AugmentWithKhopMasks(GraphModification):
    """
    Should be used as pretransform, because it is deterministic
    """

    def __init__(self, k: int):
        super(AugmentWithKhopMasks, self).__init__()
        self.khop = k

    def __call__(self, graph: Data):
        np_mask = parallel_khop_neighbor(graph.edge_index.numpy(), graph.num_nodes, self.khop)
        graph.node_mask = torch.from_numpy(np_mask).reshape(-1).to(torch.bool)
        return graph


class AugmentWithShortedPathDistance(GraphModification):
    def __init__(self, max_num_nodes):
        super(AugmentWithShortedPathDistance, self).__init__()
        self.max_num_nodes = max_num_nodes

    def __call__(self, graph: Data):
        assert is_undirected(graph.edge_index, num_nodes=graph.num_nodes)
        edge_index = graph.edge_index.numpy()
        mat = csr_matrix((np.ones(edge_index.shape[1]), (edge_index[0], edge_index[1])),
                         shape=(graph.num_nodes, graph.num_nodes))

        g_dist_mat = torch.zeros(graph.num_nodes, self.max_num_nodes, dtype=torch.float)
        g_dist_mat[:, :graph.num_nodes] = torch.from_numpy(shortest_path(mat, directed=False, return_predecessors=False, ))
        g_dist_mat[torch.isinf(g_dist_mat)] = 0.
        g_dist_mat /= g_dist_mat.max() + 1

        graph.g_dist_mat = g_dist_mat
        return graph


class RandomSampleTopk(GraphModification):
    def __init__(self, k: int, ensemble: int):
        super(RandomSampleTopk, self).__init__()
        self.k = k
        self.ensemble = ensemble

    def __call__(self, graph: Data):
        if isinstance(self.k, float):
            k = int(ceil(self.k * graph.num_nodes))
        elif isinstance(self.k, int):
            k = self.k
        else:
            raise TypeError

        if k >= graph.num_nodes:
            return collate(graph.__class__, [graph] * self.ensemble, increment=True, add_batch=False)[0]

        data_list = []
        for c in range(self.ensemble):
            mask = torch.zeros(graph.num_nodes, dtype=torch.bool)
            mask[graph.target_mask] = True
            candidates = random.sample(range(graph.num_nodes), k)
            mask[candidates] = True
            edge_index, edge_attr = pyg_subgraph(subset=mask,
                                                 edge_index=graph.edge_index,
                                                 edge_attr=graph.edge_attr,
                                                 relabel_nodes=True,
                                                 num_nodes=graph.num_nodes)
            new_data = Data(x=graph.x[mask],
                            y=graph.y,
                            edge_index=edge_index,
                            edge_attr=edge_attr,
                            # num_nodes=mask.sum(),
                            target_mask=graph.target_mask[mask])
            for k, v in graph:
                if k not in ['x', 'edge_index', 'edge_attr', 'num_nodes', 'target_mask', 'y']:
                    new_data[k] = v
            data_list.append(new_data)

        return collate(graph.__class__, data_list, increment=True, add_batch=False)[0]


def policy2transform(policy: str, sample_k: int, ensemble: int = 1) -> GraphModification:
    """
    transform for datasets

    :param policy:
    :param sample_k:
    :param ensemble:
    :return:
    """
    if policy == 'greedy_neighbors':
        return AugmentWithRandomKNeighbors(sample_k, ensemble)
    elif policy == 'khop':
        return AugmentWithKhopMasks(sample_k)
    elif policy == 'topk':
        return RandomSampleTopk(sample_k, ensemble)
    else:
        raise NotImplementedError
