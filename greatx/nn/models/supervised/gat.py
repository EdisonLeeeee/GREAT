from typing import List

import torch.nn as nn
from torch_geometric.nn import GATConv

from greatx.nn.layers import Sequential, activations
from greatx.utils import wrapper


class GAT(nn.Module):
    r"""Graph Attention Networks (GAT) from the
    `"Graph Attention Networks"
    <https://arxiv.org/abs/1710.10903>`_ paper (ICLR'19)

    Parameters
    ----------
    in_channels : int,
        the input dimensions of model
    out_channels : int,
        the output dimensions of model
    hids : List[int], optional
        the number of hidden units for each hidden layer, by default [8]
    num_heads : List[int], optional
        the number of attention heads for each hidden layer, by default [8]
    acts : List[str], optional
        the activation function for each hidden layer, by default ['relu']
    dropout : float, optional
        the dropout ratio of model, by default 0.6
    bias : bool, optional
        whether to use bias in the layers, by default True
    bn: bool, optional
        whether to use :class:`BatchNorm1d` after the convolution layer,
        by default False


    Examples
    --------
    >>> # GAT with one hidden layer
    >>> model = GAT(100, 10)

    >>> # GAT with two hidden layers
    >>> model = GAT(100, 10, hids=[32, 16], acts=['relu', 'elu'])

    >>> # GAT with two hidden layers, without first activation
    >>> model = GAT(100, 10, hids=[32, 16], acts=[None, 'relu'])

    >>> # GAT with deep architectures, each layer has elu activation
    >>> model = GAT(100, 10, hids=[16]*8, acts=['elu'])

    Reference:

    * Paper: https://arxiv.org/abs/1710.10903
    * Author's code: https://github.com/PetarV-/GAT
    * Pytorch implementation: https://github.com/Diego999/pyGAT

    """
    @wrapper
    def __init__(self, in_channels: int, out_channels: int,
                 hids: List[int] = [8], num_heads: List[int] = [8],
                 acts: List[str] = ['elu'], dropout: float = 0.6,
                 bias: bool = True, bn: bool = False, includes=['num_heads']):
        super().__init__()
        head = 1
        conv = []
        for hid, num_head, act in zip(hids, num_heads, acts):
            conv.append(
                GATConv(in_channels * head, hid, heads=num_head, bias=bias,
                        dropout=dropout))
            if bn:
                conv.append(nn.BatchNorm1d(hid * num_head))
            conv.append(activations.get(act))
            conv.append(nn.Dropout(dropout))
            in_channels = hid
            head = num_head

        conv.append(
            GATConv(in_channels * head, out_channels, heads=1, bias=bias,
                    concat=False, dropout=dropout))

        self.conv = Sequential(*conv)

    def reset_parameters(self):
        self.conv.reset_parameters()

    def forward(self, x, edge_index, edge_weight=None):
        """"""
        return self.conv(x, edge_index, edge_weight)
