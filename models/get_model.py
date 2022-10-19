from .ogb_mol_gnn import OGBGNN, OGBGNN_inner
from .emb_model import UpStream
from data.const import DATASET_FEATURE_STAT_DICT


def get_model(args, *_args):
    if args.model.lower() == 'ogb_gin':
        model = OGBGNN(gnn_type='gin',
                       num_tasks=DATASET_FEATURE_STAT_DICT[args.dataset]['num_class'],
                       num_layer=args.num_convlayers,
                       emb_dim=args.hid_size,
                       drop_ratio=args.dropout,
                       virtual_node=False)

        inner_model = OGBGNN_inner(gnn_type='gin',
                                   num_layer=args.sample_configs.inner_layer,
                                   emb_dim=args.hid_size,
                                   drop_ratio=args.dropout,
                                   subgraph2node_aggr=args.sample_configs.subgraph2node_aggr,
                                   virtual_node=False)
    else:
        raise NotImplementedError

    if args.imle_configs is not None:
        emb_model = UpStream(hid_size=args.imle_configs.emb_hid_size,
                             num_layer=args.imle_configs.emb_num_layer)
    else:
        emb_model = None

    return model, emb_model, inner_model
