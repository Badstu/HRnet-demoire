from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import logging

import torch
import torch.nn as nn
import numpy as np


class L1_Charbonnier_loss(nn.Module):
    """L1 Charbonnierloss."""
    def __init__(self):
        super(L1_Charbonnier_loss, self).__init__()
        self.eps = 1e-6

    def forward(self, X, Y):
        diff = torch.add(X, -Y)
        error = torch.sqrt(diff * diff + self.eps)
        loss = torch.sum(error) / X.size(0)
        return loss


class L1_Sobel_Loss(nn.Module):
    def __init__(self, device=torch.device('cuda')):
        super(L1_Sobel_Loss, self).__init__()
        self.device = device
        self.conv_op_x = nn.Conv2d(1, 1, 3, bias=False)
        self.conv_op_y = nn.Conv2d(1, 1, 3, bias=False)

        sobel_kernel_x = np.array([[[1, 0, -1], [2, 0, -2], [1, 0, -1]],
                                   [[1, 0, -1], [2, 0, -2], [1, 0, -1]],
                                   [[1, 0, -1], [2, 0, -2], [1, 0, -1]]], dtype='float32')
        sobel_kernel_y = np.array([[[1, 2, 1], [0, 0, 0], [-1, -2, -1]],
                                   [[1, 2, 1], [0, 0, 0], [-1, -2, -1]],
                                   [[1, 2, 1], [0, 0, 0], [-1, -2, -1]]], dtype='float32')
        sobel_kernel_x = sobel_kernel_x.reshape((1, 3, 3, 3))
        sobel_kernel_y = sobel_kernel_y.reshape((1, 3, 3, 3))

        self.conv_op_x.weight.data = torch.from_numpy(sobel_kernel_x).to(device)
        self.conv_op_y.weight.data = torch.from_numpy(sobel_kernel_y).to(device)
        self.conv_op_x.weight.requires_grad = False
        self.conv_op_y.weight.requires_grad = False

    def forward(self, source, target):

        loss = 0
        edge_X_x = self.conv_op_x(source)
        edge_X_y = self.conv_op_y(source)
        edge_Y_x = self.conv_op_x(target)
        edge_Y_y = self.conv_op_y(target)
        edge_X = torch.sqrt(edge_X_x * edge_X_x + edge_X_y * edge_X_y)
        edge_Y = torch.sqrt(edge_Y_x * edge_Y_x + edge_Y_y * edge_Y_y)

        diff = torch.add(edge_X, -edge_Y)
        error = torch.sqrt(diff * diff)
        loss = torch.sum(error)
        loss /= source.size(0)

        return loss


class Weighted_Loss(nn.Module):
    def __init__(self):
        super(Weighted_Loss, self).__init__()
        self.Charbonnier_loss = L1_Charbonnier_loss()
        self.Sobel_Loss = L1_Sobel_Loss(device=torch.device('cuda'))

    def forward(self, X, Y):
        c_loss = self.Charbonnier_loss(X, Y)
        s_loss = self.Sobel_Loss(X, Y)
        loss = c_loss * 0.5 + s_loss * 0.5
        return s_loss
