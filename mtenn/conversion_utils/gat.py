"""
Representation and strategy for GAT model.
"""
from copy import deepcopy
import torch
from dgllife.model import GAT as GAT_dgl
from dgllife.model import WeightedSumAndMax

from mtenn.model import LigandOnlyModel


class GAT(torch.nn.Module):
    def __init__(self, *args, model=None, **kwargs):
        super().__init__()

        # First check for predictor_hidden_feats so it doesn't get passed to DGL GAT
        #  constructor
        predictor_hidden_feats = kwargs.pop("predictor_hidden_feats", None)

        ## If no model is passed, construct model based on passed args, otherwise copy
        ##  all parameters and weights over
        if model is None:
            self.gnn = GAT_dgl(*args, **kwargs)
        else:
            # Parameters that are conveniently accessible from the top level
            in_feats = model.gnn_layers[0].gat_conv.fc.in_features
            hidden_feats = model.hidden_feats
            num_heads = model.num_heads
            agg_modes = model.agg_modes
            # Parameters that can only be adcessed layer-wise
            layer_params = []
            for l in model.gnn_layers:
                gc = l.gat_conv
                new_params = (
                    gc.feat_drop.p,
                    gc.attn_drop.p,
                    gc.leaky_relu.negative_slope,
                    gc.activation,
                    bool(gc.res_fc),
                    (gc.res_fc.bias is not None)
                    if gc.has_linear_res
                    else gc.has_explicit_bias,
                )
                layer_params += [new_params]

            (
                feat_drops,
                attn_drops,
                alphas,
                activations,
                residuals,
                biases,
            ) = zip(*layer_params)
            self.gnn = GAT_dgl(
                in_feats=in_feats,
                hidden_feats=hidden_feats,
                num_heads=num_heads,
                feat_drops=feat_drops,
                attn_drops=attn_drops,
                alphas=alphas,
                residuals=residuals,
                agg_modes=agg_modes,
                activations=activations,
                biases=biases,
            )
            self.gnn.load_state_dict(model.state_dict())

        # Copied from GATPredictor class, figure out how many features the last
        #  layer of the GNN will have
        if self.gnn.agg_modes[-1] == "flatten":
            gnn_out_feats = self.gnn.hidden_feats[-1] * self.gnn.num_heads[-1]
        else:
            gnn_out_feats = self.gnn.hidden_feats[-1]
        self.readout = WeightedSumAndMax(gnn_out_feats)

        # Use given hidden feats if supplied, otherwise use 1/2 gnn_out_feats
        if predictor_hidden_feats is None:
            predictor_hidden_feats = gnn_out_feats // 2

        # 2 layer MLP with ReLU activation (borrowed from GATPredictor)
        self.predict = torch.nn.Sequential(
            torch.nn.Linear(2 * gnn_out_feats, predictor_hidden_feats),
            torch.nn.ReLU(),
            torch.nn.Linear(predictor_hidden_feats, 1),
        )

    def forward(self, data):
        g = data["g"]
        node_feats = self.gnn(g, g.ndata["h"])
        graph_feats = self.readout(g, node_feats)
        return self.predict(graph_feats)

    def _get_representation(self):
        """
        Input model, remove last layer.

        Returns
        -------
        GAT
            Copied GAT model with the last layer replaced by an Identity module
        """

        ## Copy model so initial model isn't affected
        model_copy = deepcopy(self.gnn)

        return model_copy

    def _get_energy_func(self):
        """
        Return last two layer of the model.

        Returns
        -------
        torch.nn.Sequential
            Sequential module calling copy of `model`'s last two layers
        """

        return torch.nn.Sequential(deepcopy(self.readout), deepcopy(self.predict))

    @staticmethod
    def get_model(
        *args,
        model=None,
        fix_device=False,
        pred_readout=None,
        **kwargs,
    ):
        """
        Exposed function to build a LigandOnlyModel object from a GAT object
        (or args/kwargs).

        Parameters
        ----------
        model: GAT, optional
            GAT model to use to build the LigandOnlyModel object. If left as none, a
            default model will be initialized and used
        fix_device: bool, default=False
            If True, make sure the input is on the same device as the model,
            copying over as necessary.
        pred_readout : Readout
            Readout object for the energy predictions. If `grouped` is `False`,
            this option will still be used in the construction of the `LigandOnlyModel`
            object.

        Returns
        -------
        LigandOnlyModel
            LigandOnlyModel object containing the desired Representation and Strategy
        """
        if model is None:
            model = GAT(*args, **kwargs)

        return LigandOnlyModel(model=model, readout=pred_readout, fix_device=fix_device)
