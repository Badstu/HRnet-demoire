from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import argparse
import time
import logging
import numpy as np
import matplotlib.pyplot as plt
import colour

import fire
import os
import sys
import ipdb
from tqdm import tqdm
import torch
import torch.nn.functional as F
from torch import nn
from torch.utils.data import DataLoader
from torchnet import meter
from torch.autograd import Variable
from torchvision import transforms, datasets
from torch.utils.checkpoint import checkpoint


from utils.visualize import Visualizer
from utils.myutils import tensor2im
from models.DnCNN import DnCNN
from data.dataset_Sun import MoireData

class Config(object):
    temp_winorserver = False
    is_dev = True if temp_winorserver else False
    is_linux = False if temp_winorserver else True
    gpu = False if temp_winorserver else True # 是否使用GPU
    device = torch.device('cuda') if gpu else torch.device('cpu')

    if is_linux == False:
        train_path = "T:\\dataset\\AIM2019 demoireing challenge\\Training\\Training"
        valid_path = "T:\\dataset\\AIM2019 demoireing challenge\\Validation"
        debug_file = 'F:\\workspaces\\demoire\\debug'  # 存在该文件则进入debug模式
    else:
        train_path = "/HDD/sayhi/dataset/TIPbenchmark/train/trainData"
        test_path = "/HDD/sayhi/dataset/TIPbenchmark/test/testData"
    label_dict = {1: "moire",
                  0: "clear"}
    num_workers = 4
    image_size = 256
    train_batch_size = 32 #train的维度为(64, 3, 256, 256) 一个batch10张照片，要1000次iter
    val_batch_size = 128
    max_epoch = 200
    lr = 1e-4
    lr_decay = 0.3
    beta1 = 0.5  # Adam优化器的beta1参数
    accumulation_steps = 1  # 梯度累加的参数
    loss_alpha = 0.8  # 两个loss的权值


    vis = False if temp_winorserver else True
    env = 'demoire-DnCNN'
    plot_every = 200 #每隔20个batch, visdom画图一次
    val_plot_every = 10

    save_every = 5  # 每5个epoch保存一次模型
    model_path = None #'checkpoints/HRnet_211.pth'
    save_prefix = "checkpoints/DnCNN/"

opt = Config()


def train(**kwargs):
    #init
    for k_, v_ in kwargs.items():
        setattr(opt, k_, v_)

    if opt.vis:
        vis = Visualizer(opt.env)
        vis_val = Visualizer('valdemoire-DnCNN')

    train_data = MoireData(opt.train_path)
    val_data = MoireData(opt.test_path, is_val=True)
    train_dataloader = DataLoader(train_data,
                            batch_size=opt.train_batch_size if opt.is_dev == False else 4,
                            shuffle=True,
                            num_workers=opt.num_workers if opt.is_dev == False else 0,
                            drop_last=True)
    val_dataloader = DataLoader(val_data,
                            batch_size=opt.val_batch_size if opt.is_dev == False else 4,
                            shuffle=True,
                            num_workers=opt.num_workers if opt.is_dev == False else 0,
                            drop_last=True)

    last_epoch = 0
    model = DnCNN()
    model = model.to(opt.device)

    # val_loss, val_psnr = val(model, val_dataloader, vis_val)
    # print(val_loss, val_psnr)

    criterion = nn.MSELoss()
    lr = opt.lr
    optimizer = torch.optim.Adam(
        model.parameters(),
        lr=lr,
        weight_decay=0.00001
    )

    if opt.model_path:
        map_location = lambda storage, loc: storage
        checkpoint = torch.load(opt.model_path, map_location=map_location)
        last_epoch = checkpoint["epoch"]
        optimizer_state = checkpoint["optimizer"]
        optimizer.load_state_dict(optimizer_state)

        lr = checkpoint["lr"]
        for param_group in optimizer.param_groups:
            param_group['lr'] = lr

    loss_meter = meter.AverageValueMeter()
    psnr_meter = meter.AverageValueMeter()
    previous_loss = 1e100

    for epoch in range(opt.max_epoch):
        if epoch < last_epoch:
            continue
        loss_meter.reset()
        psnr_meter.reset()
        loss_list = []

        for ii, (moires, clear_list) in tqdm(enumerate(train_dataloader)):
            if ii > 1000:
                break
            moires = moires.to(opt.device)
            clears = clear_list[0].to(opt.device)

            outputs = model(moires)
            loss = criterion(outputs, clears)

            loss.backward()
            optimizer.step()
            optimizer.zero_grad()

            loss_meter.add(loss.item())

            moires = tensor2im(moires)
            outputs = tensor2im(outputs)
            clears = tensor2im(clears)

            psnr = colour.utilities.metric_psnr(outputs, clears)
            psnr_meter.add(psnr)

            if opt.vis and (ii + 1) % opt.plot_every == 0: #20个batch画图一次
                vis.images(moires, win='moire_image')
                vis.images(outputs, win='output_image')
                vis.text("current outputs_size:{outputs_size},<br/> outputs:{outputs}<br/>".format(
                                                                                    outputs_size=outputs.shape,
                                                                                    outputs=outputs), win="size")
                vis.images(clears, win='clear_image')
                vis.plot('train_loss', loss_meter.value()[0]) #meter.value() return 2 value of mean and std
                vis.log("epoch:{epoch}, lr:{lr}, train_loss:{loss}, train_psnr:{train_psnr}".format(epoch=epoch+1,
                                                                                          loss=loss_meter.value()[0],
                                                                                          lr=lr,
                                                                                          train_psnr = psnr_meter.value()[0]))
                # record the train loss to txt
                loss_list.append(str(loss_meter.value()[0]))

        val_loss, val_psnr = val(model, val_dataloader, vis_val)
        if opt.vis:
            vis.plot('val_loss', val_loss)
            vis.log("epoch:{epoch}, average val_loss:{val_loss}, average val_psnr:{val_psnr}".format(epoch=epoch+1,
                                                                                            val_loss=val_loss,
                                                                                            val_psnr=val_psnr))
        # 每个epoch把loss写入文件
        with open(opt.save_prefix+"loss_list.txt", 'a') as f:
            f.write("\nepoch_{}\n".format(epoch+1))
            f.write('\n'.join(loss_list))

        if (epoch + 1) % opt.save_every == 0 or epoch == 0: # 10个epoch保存一次
            prefix = opt.save_prefix+'HRnet_epoch{}_'.format(epoch+1)
            file_name = time.strftime(prefix + '%m%d_%H_%M_%S.pth')
            checkpoint = {
                'epoch': epoch + 1,
                "optimizer": optimizer.state_dict(),
                "model": model.state_dict(),
                "lr": lr
            }
            torch.save(checkpoint, file_name)

        if loss_meter.value()[0] > previous_loss:
            lr = lr * opt.lr_decay
            for param_group in optimizer.param_groups:
                param_group['lr'] = lr
        previous_loss = loss_meter.value()[0]


    prefix = opt.save_prefix+'HRnet_final_'
    file_name = time.strftime(prefix + '%m%d_%H_%M_%S.pth')
    checkpoint = {
        'epoch': epoch + 1,
        "optimizer": optimizer.state_dict(),
        "model": model.state_dict(),
        "lr": lr
    }
    torch.save(checkpoint, file_name)


@torch.no_grad()
def val(model, dataloader, vis=None):
    model.eval()
    torch.cuda.empty_cache()

    criterion = nn.MSELoss()

    loss_meter = meter.AverageValueMeter()
    psnr_meter = meter.AverageValueMeter()
    vis.log("~~~~~~~~~~~~~~~~~~start~~~~~~~~~~~~~~~~~~~~~~")
    for ii, (val_moires, val_clears) in tqdm(enumerate(dataloader)):
        val_moires = val_moires.to(opt.device)
        val_clears = val_clears.to(opt.device)
        val_outputs = model(val_moires)

        val_loss = criterion(val_outputs, val_clears)
        loss_meter.add(val_loss.item())

        val_moires = tensor2im(val_moires)
        val_outputs = tensor2im(val_outputs)
        val_clears = tensor2im(val_clears)

        val_psnr = colour.utilities.metric_psnr(val_outputs, val_clears)
        psnr_meter.add(val_psnr)

        if opt.vis and vis != None and (ii + 1) % opt.val_plot_every == 0:  # 每个个iter画图一次
            # vis.images(val_moires, win='val_moire_image')
            # vis.images(val_outputs, win='val_output_image')
            # vis.images(val_clears, win='val_clear_image')

            vis.log(">>>>>>>> val_loss:{val_loss}, val_psnr:{val_psnr}".format(val_loss=val_loss,
                                                                             val_psnr=val_psnr))
    vis.log("~~~~~~~~~~~~~~~~~~end~~~~~~~~~~~~~~~~~~~~~~")

    model.train()
    return loss_meter.value()[0], psnr_meter.value()[0]


if __name__ == '__main__':
    train()