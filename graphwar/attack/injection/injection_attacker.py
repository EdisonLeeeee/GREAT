from functools import lru_cache
from typing import Optional, Union

from copy import copy
import torch
import numpy as np
from torch import Tensor

from graphwar.attack.attacker import Attacker
from graphwar.utils import add_edges
from torch_geometric.data import Data


class InjectionAttacker(Attacker):
    """Base class for Injection Attacker, an inherent attack should implement 
    the `attack` method.

    Example
    -------
    >>> attacker = InjectionAttacker(data)
    >>> attacker.reset()
    # inject 10 nodes, where each nodes has 2 edges
    >>> attacker.attack(num_budgets=10, num_edges_local=2) 
    # inject 10 nodes, with 100 edges in total
    >>> attacker.attack(num_budgets=10, num_edges_global=100) 
    # inject 10 nodes, where each nodes has 2 edges, 
    # the features of injected nodes lies in [0,1]
    >>> attacker.attack(num_budgets=10, num_edges_local=2, feat_limits=(0,1)) 
    >>> attacker.attack(num_budgets=10, num_edges_local=2, feat_limits={'min': 0, 'max':1}) 
    # inject 10 nodes, where each nodes has 2 edges, 
    # the features of injected each node has 10 nonzero elements
    >>> attacker.attack(num_budgets=10, num_edges_local=2, feat_budgets=10) 

    # get injected nodes
    >>> attacker.injected_nodes()
    # get injected edges
    >>> attacker.injected_edges()
    # get injected nodes' features
    >>> attacker.injected_feats()
    # get perturbed graph
    >>> attacker.data()
    """

    def reset(self) -> "InjectionAttacker":
        """Reset the state of the Attacker

        Returns
        -------
        InjectionAttacker
            the attacker itself
        """
        super().reset()
        self.num_budgets = None
        self.feat_limits = None
        self.feat_budgets = None
        self.num_edges_global = None
        self.num_edges_local = None
        self._injected_nodes = []
        self._injected_edges = {}
        self._injected_feats = []
        self.data.cache_clear()

        return self

    def attack(self, num_budgets: Union[int, float], *, targets: Optional[Tensor] = None, num_edges_global: Optional[int] = None,
               num_edges_local: Optional[int] = None,
               feat_limits: Optional[Union[tuple, dict]] = None,
               feat_budgets: Optional[int] = None) -> "InjectionAttacker":
        """Base method that describes the adversarial injection attack

        Parameters
        ----------
        num_budgets : Union[int, float]
            the number/percentage of nodes allowed to inject
        targets : Optional[Tensor], optional
            the targeted nodes where injected nodes perturb,
            if None, it will be all nodes in the graph, by default None
        interconnection : bool, optional
            whether the injected nodes can connect to each other, by default False
        num_edges_global : Optional[int], optional
            the number of total edges to be injected for all injected nodes, by default None
        num_edges_local : Optional[int], optional
            the number of edges allowed to inject for each injected nodes, by default None
        feat_limits : Optional[Union[tuple, dict]], optional
            the limitation or allowed budgets of injected node features,
            it can be a tuple, e.g., `(0, 1)` or 
            a dict, e.g., `{'min':0, 'max': 1}`,
        feat_budgets :  Optional[int], optional
            the number of features can be flipped for each node,
            e.g., `10`, denoting 10 features can be flipped, by default None
        disable : bool, optional
            whether the tqdm progbar is to disabled, by default False

        Returns
        -------
        InjectionAttacker
            the attacker itself
        """

        _is_setup = getattr(self, "_is_setup", True)

        if not _is_setup:
            raise RuntimeError(
                f'{self.name} requires a surrogate model to conduct attack. '
                'Use `attacker.setup_surrogate(surrogate_model)`.')

        if not self._is_reset:
            raise RuntimeError(
                'Before calling attack, you must reset your attacker. Use `attacker.reset()`.'
            )

        num_budgets = self._check_budget(
            num_budgets, max_perturbations=self.num_nodes)

        if targets is None:
            self.targets = list(range(self.num_nodes))
        else:
            if isinstance(targets, torch.BoolTensor):
                # Boolean mask
                self.targets = targets.nonzero().view(-1).tolist()
            else:
                # node indices
                self.targets = torch.LongTensor(targets).view(-1).tolist()

        if num_edges_local is not None and num_edges_global is not None:
            raise RuntimeError(
                "Both `num_edges_local` and `num_edges_global` cannot be used simultaneously.")

        if num_edges_global is not None:
            num_edges_local = num_edges_global // len(self.targets)
            if num_edges_local == 0:
                raise ValueError(
                    f"Too less edges allowed (num_edges_global={num_edges_global}) for injected nodes ({len(self.targets)}). "
                    "Maybe you could use the argument `num_edges_local` instead.")

        if num_edges_local is None:
            num_edges_local = int(self._degree.mean().clamp(min=1))

        self.num_budgets = num_budgets
        self.num_edges_global = num_edges_global
        self.num_edges_local = num_edges_local

        # ============== get feature limitation of injected node ==============
        min_limits = max_limits = None

        if feat_limits is not None and feat_budgets is not None:
            raise RuntimeError(
                "Both `feat_limits` and `feat_budgets` cannot be used simultaneously.")

        if feat_limits is not None:
            if isinstance(feat_limits, tuple):
                min_limits, max_limits = feat_limits
            elif isinstance(feat_limits, dict):
                min_limits = feat_limits.pop('min', None)
                max_limits = feat_limits.pop('max', None)
                if feat_limits:
                    raise ValueError(
                        f"Unrecognized key {next(iter(feat_limits.keys()))}.")
            else:
                raise TypeError(
                    f"`feat_limits` should be an instance of tuple and dict, but got {feat_limits}.")

        feat = self.feat
        assert feat is not None

        if min_limits is None and feat is not None:
            min_limits = feat.min()
        else:
            min_limits = 0.

        if max_limits is None and feat is not None:
            max_limits = feat.max()
        else:
            max_limits = 1.

        if feat_budgets is not None:
            assert feat_budgets <= self.num_feats

        self.feat_budgets = feat_budgets

        # TODO
        self._mu = (max_limits - min_limits) / 2
        self._sigma = (max_limits - self._mu) / 3  # 3-sigma rule
        # ======================================================================

        self.feat_limits = min_limits, max_limits

        self._is_reset = False

        return self

    def injected_nodes(self) -> Optional[Tensor]:
        """Get all the nodes to be injected."""
        nodes = self._injected_nodes
        if nodes is None or len(nodes) == 0:
            return None

        if torch.is_tensor(nodes):
            return nodes.to(self.device)

        if isinstance(nodes, dict):
            nodes = sorted(list(nodes.keys()))

        return torch.tensor(np.asarray(nodes, dtype="int64"), device=self.device)

    def added_nodes(self) -> Optional[Tensor]:
        """alias of method `added_nodes`"""
        return self.injected_nodes()

    def injected_edges(self) -> Optional[Tensor]:
        """Get all the edges to be injected."""
        edges = self._injected_edges
        if edges is None or len(edges) == 0:
            return None

        if torch.is_tensor(edges):
            return edges.to(self.device)

        if isinstance(edges, dict):
            edges = list(edges.keys())

        return torch.tensor(np.asarray(edges, dtype="int64").T, device=self.device)

    def added_edges(self) -> Optional[Tensor]:
        """alias of method `injected_edges`"""
        return self.injected_edges()

    def injected_feats(self) -> Optional[Tensor]:
        """Get the features injected nodes."""
        feats = self._injected_feats
        if feats is None or len(feats) == 0:
            return None
        # feats = list(self._injected_nodes.values())
        return torch.stack(feats, dim=0).float().to(self.device)

    def added_feats(self) -> Optional[Tensor]:
        """alias of method `added_edges`"""
        return self.injected_feats()

    def inject_node(self, node, feat: Optional[Tensor] = None):
        if feat is None:
            if self.feat_budgets is not None:
                # For boolean features, we generate it
                # randomly flip features along the feature dimension
                feat = self.feat.new_zeros(1, self.num_feats)
                idx = torch.randperm(self.num_feats)[:self.feat_budgets]
                feat[idx] = 1.0
            else:
                # For continuos features, we generate it
                # following uniform distribution
                feat = self.feat.new_empty(
                    self.num_feats).uniform_(*self.feat_limits)
        else:
            if self.feat_budgets is not None:
                assert feat.bool().sum() <= self.feat_budgets
            else:
                assert feat.min() >= self.feat_limits[0]
                assert feat.max() <= self.feat_limits[1]

        self._injected_nodes.append(node)
        self._injected_feats.append(feat)

    def inject_edge(self, u: int, v: int, it: Optional[int] = None):
        """Inject an edge to the graph.

        Parameters
        ----------
        u : int
            The source node of the edge.
        v : int
            The destination node of the edge.
        it : Optional[int], optional
            The iteration that indicates the order of the edge being added, by default None
        """

        self._injected_edges[(u, v)] = it

    @lru_cache(maxsize=1)
    def data(self, symmetric: bool = True) -> Data:
        """return the attacked graph

        Parameters
        ----------
        symmetric : bool
            Determine whether the resulting graph is forcibly symmetric

        Returns
        -------
        Data
            the attacked graph represented as PyG-like data
        """
        data = copy(self.ori_data)
        # injected_nodes = self.injected_nodes()
        injected_edges = self.injected_edges()
        injected_feats = self.injected_feats()
        data.x = torch.cat([data.x, injected_feats], dim=0)
        data.edge_index = add_edges(
            data.edge_index, injected_edges, symmetric=symmetric)

        return data
