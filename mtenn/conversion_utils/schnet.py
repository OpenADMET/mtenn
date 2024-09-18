"""
``Representation`` and ``Strategy`` implementations for the SchNet model architecture.
The underlying model that we use is the implementation in
`PyTorch Geometric <https://pytorch-geometric.readthedocs.io/en/latest/generated/
torch_geometric.nn.models.SchNet.html#torch_geometric.nn.models.SchNet>`_.
"""
from copy import deepcopy
import torch
from torch_geometric.nn.models import SchNet as PygSchNet

from mtenn.model import GroupedModel, Model
from mtenn.strategy import ComplexOnlyStrategy, ConcatStrategy, DeltaStrategy


class SchNet(PygSchNet):
    """
    ``mtenn`` wrapper around the PyTorch Geometric SchNet model. This class handles
    construction of the model and the formatting into ``Representation`` and
    ``Strategy`` blocks.
    """

    def __init__(self, *args, model=None, **kwargs):
        """
        Initialize the underlying ``torch_geometric.nn.models.SchNet`` model. If a value
        is passed for ``model``, builds a new ``torch_geometric.nn.models.SchNet`` model
        based on those hyperparameters, and copies over the weights. Otherwise, all
        ``*args`` and ``**kwargs`` are passed directly to the
        ``torch_geometric.nn.models.SchNet`` constructor.

        Parameters
        ----------
        model : ``torch_geometric.nn.models.SchNet``, optional
            PyTorch Geometric SchNet model to use to construct the underlying model
        """
        # If no model is passed, construct default SchNet model, otherwise copy
        #  all parameters and weights over
        if model is None:
            super(SchNet, self).__init__(*args, **kwargs)
        else:
            try:
                atomref = model.atomref.weight.detach().clone()
            except AttributeError:
                atomref = None
            model_params = (
                model.hidden_channels,
                model.num_filters,
                model.num_interactions,
                model.num_gaussians,
                model.cutoff,
                model.interaction_graph,
                model.interaction_graph.max_num_neighbors,
                model.readout,
                model.dipole,
                model.mean,
                model.std,
                atomref,
            )
            super(SchNet, self).__init__(*model_params)
            self.load_state_dict(model.state_dict())

    def forward(self, data):
        """
        Make a prediction of the target property based on an input structure.

        Parameters
        ----------
        data : dict[str, torch.Tensor]
            This dictionary should at minimum contain entries for:

            * ``"pos"``: Atom coordinates

            * ``"z"``: Atomic numbers

        Returns
        -------
        torch.Tensor
            Model prediction
        """
        return super(SchNet, self).forward(data["z"], data["pos"])

    def _get_representation(self):
        """
        Copy model and set last layer as the ``Identity``.

        Parameters
        ----------
        model: mtenn.conversion_utils.schnet.SchNet
            ``SchNet`` model

        Returns
        -------
        mtenn.conversion_utils.schnet.SchNet
            Copied ``SchNet`` model with the last layer replaced by the ``Identity``
        """

        # Copy model so initial model isn't affected
        model_copy = deepcopy(self)
        # Replace final linear layer with an identity module
        model_copy.lin2 = torch.nn.Identity()

        return model_copy

    def _get_energy_func(self):
        """
        Return copy of last layer of the model.

        Parameters
        ----------
        model: mtenn.conversion_utils.schnet.SchNet
            ``SchNet`` model

        Returns
        -------
        torch.nn.modules.linear.Linear
            Copy of last layer
        """

        return deepcopy(self.lin2)

    def _get_delta_strategy(self):
        """
        Build a :py:class:`DeltaStrategy <mtenn.strategy.DeltaStrategy>` object based on
        the calling model.

        Returns
        -------
        mtenn.strategy.DeltaStrategy
            ``DeltaStrategy`` built from the model
        """

        return DeltaStrategy(self._get_energy_func())

    def _get_complex_only_strategy(self):
        """
        Build a :py:class:`ComplexOnlyStrategy <mtenn.strategy.ComplexOnlyStrategy>`
        object based on the calling model.

        Returns
        -------
        mtenn.strategy.ComplexOnlyStrategy
            ``ComplexOnlyStrategy`` built from the model
        """

        return ComplexOnlyStrategy(self._get_energy_func())

    def _get_concat_strategy(self):
        """
        Build a :py:class:`ConcatStrategy <mtenn.strategy.ConcatStrategy>` object with
        the appropriate ``input_size``.

        Returns
        -------
        ConcatStrategy
            ``ConcatStrategy`` for the model
        """

        # Calculate input size as 3 * dimensionality of output of Representation
        #  (ie lin1 layer)
        input_size = 3 * self.lin1.out_features
        return ConcatStrategy(input_size=input_size)

    @staticmethod
    def get_model(
        model=None,
        grouped=False,
        fix_device=False,
        strategy: str = "delta",
        combination=None,
        pred_readout=None,
        comb_readout=None,
    ):
        """
        Exposed function to build a :py:class:`Model <mtenn.model.Model>` or
        :py:class:`GroupedModel <mtenn.model.GroupedModel>` from a :py:class:`SchNet
        <mtenn.conversion_utils.schnet.SchNet>` (or args/kwargs). If no ``model`` is
        given, build a default ``SchNet`` model.

        Parameters
        ----------
        model: mtenn.conversion_utils.schnet.SchNet, optional
            ``SchNet`` model to use to build the ``Model`` object. If not given, build a
            default model
        grouped: bool, default=False
            Build a ``GroupedModel``
        fix_device: bool, default=False
            If True, make sure the input is on the same device as the model,
            copying over as necessary
        strategy: str, default='delta'
            ``Strategy`` to use to combine representations of the different parts.
            Options are [``delta``, ``concat``, ``complex``]
        combination: mtenn.combination.Combination, optional
            ``Combination`` object to use to combine multiple predictions. A value must
            be passed if ``grouped`` is ``True``
        pred_readout : mtenn.readout.Readout, optional
            ``Readout`` object for the individual energy predictions. If a
            ``GroupedModel`` is being built, this ``Readout`` will be applied to each
            individual prediction before the values are passed to the ``Combination``.
            If a ``Model`` is being built, this will be applied to the single prediction
            before it is returned
        comb_readout : mtenn.readout.Readout, optional
            Readout object for the combined multi-pose prediction, in the case that a
            ``GroupedModel`` is being built. Otherwise, this is ignored

        Returns
        -------
        mtenn.model.Model
            ``Model`` or ``GroupedModel`` containing the desired ``Representation``,
            ``Strategy``, and ``Combination`` and ``Readout`` s as desired
        """
        if model is None:
            model = SchNet()

        # First get representation module
        representation = model._get_representation()

        # Construct strategy module based on model and
        #  representation (if necessary)
        strategy = strategy.lower()
        if strategy == "delta":
            strategy = model._get_delta_strategy()
        elif strategy == "concat":
            strategy = model._get_concat_strategy()
        elif strategy == "complex":
            strategy = model._get_complex_only_strategy()
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

        # Check on `combination`
        if grouped and (combination is None):
            raise ValueError(
                "Must pass a value for `combination` if `grouped` is `True`."
            )

        if grouped:
            return GroupedModel(
                representation,
                strategy,
                combination,
                pred_readout,
                comb_readout,
                fix_device,
            )
        else:
            return Model(representation, strategy, pred_readout, fix_device)
