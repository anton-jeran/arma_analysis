# --------------------------------------------------------
# Pytorch multi-GPU Faster R-CNN
# Licensed under The MIT License [see LICENSE for details]
# Written by Jiasen Lu, Jianwei Yang, based on code from Ross Girshick
# --------------------------------------------------------
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import _init_paths
import os
import sys
import numpy as np
import argparse
import pprint
import pdb
import time
import collections
import hashlib
import json
import warnings
warnings.filterwarnings("ignore")

import torch
from torch.autograd import Variable
import torch.nn as nn
import torch.optim as optim
from emailer import send_message

import torchvision.transforms as transforms
from torch.utils.data.sampler import Sampler

from roi_data_layer.roidb import combined_roidb
from roi_data_layer.roibatchLoader import roibatchLoader
from model.utils.config import cfg, cfg_from_file, cfg_from_list, get_output_dir
from model.utils.net_utils import weights_normal_init, save_net, load_net, \
      adjust_learning_rate, warmup_learning_rate, save_checkpoint, clip_gradient

from model.faster_rcnn.vgg16 import vgg16
from model.faster_rcnn.resnet import resnet
from model.faster_rcnn.lth import lth

def parse_args():
  """
  Parse input arguments
  """
  parser = argparse.ArgumentParser(description='Train a Fast R-CNN network')
  parser.add_argument('--dataset', dest='dataset',
                      help='training dataset',
                      default='pascal_voc', type=str)
  parser.add_argument('--net', dest='net',
                    help='vgg16, res101',
                    default='vgg16', type=str)
  parser.add_argument('--start_epoch', dest='start_epoch',
                      help='starting epoch',
                      default=1, type=int)
  parser.add_argument('--epochs', dest='max_epochs',
                      help='number of epochs to train',
                      default=20, type=int)
  parser.add_argument('--disp_interval', dest='disp_interval',
                      help='number of iterations to display',
                      default=100, type=int)
  parser.add_argument('--checkpoint_interval', dest='checkpoint_interval',
                      help='number of iterations to display',
                      default=10000, type=int)

  parser.add_argument('--pretrained_path', dest='pretrained_path',
                      help='path to checkpoint containing pretrained imagenet model', default='',
                      type=str)
  parser.add_argument('--save_dir', dest='save_dir',
                      help='directory to save models', default="models",
                      type=str)
  parser.add_argument('--nw', dest='num_workers',
                      help='number of worker to load data',
                      default=0, type=int)
  parser.add_argument('--cuda', dest='cuda',
                      help='whether use CUDA',
                      action='store_true')
  parser.add_argument('--ls', dest='large_scale',
                      help='whether use large imag scale',
                      action='store_true')                      
  parser.add_argument('--mGPUs', dest='mGPUs',
                      help='whether use multiple GPUs',
                      action='store_true')
  parser.add_argument('--bs', dest='batch_size',
                      help='batch_size',
                      default=1, type=int)
  parser.add_argument('--cag', dest='class_agnostic',
                      help='whether perform class_agnostic bbox regression',
                      action='store_true')
  parser.add_argument('--rs', dest='random_seed',
                      help='seed for random operations',
                      default=0, type=int)

# config optimization
  parser.add_argument('--o', dest='optimizer',
                      help='training optimizer',
                      default="sgd", type=str)
  parser.add_argument('--lr', dest='lr',
                      help='starting learning rate',
                      default=0.001, type=float)
  parser.add_argument('--lr_decay_step', dest='lr_decay_step',
                      help='step to do learning rate decay, unit is epoch',
                      default=5, type=int)
  parser.add_argument('--lr_decay_gamma', dest='lr_decay_gamma',
                      help='learning rate decay ratio',
                      default=0.1, type=float)
  parser.add_argument('--lr_warmup', dest='lr_warmup',
                      help='warmup period for learning rate',
                      default=0, type=int)

#lth
  parser.add_argument('--kp', dest='keep_percentage',
                      help='percentage of weights to keep (0-1)',
                      default=1.0, type=float)
  parser.add_argument('--nr', dest='num_rounds',
                      help='number of rounds for iterative pruning',
                      default=1, type=int)
  parser.add_argument('--lri', dest='late_reset_iter',
                      help='iteration number of initial model training to reset to',
                      default=0, type=int)
  parser.add_argument('--ml', dest='module_list',
                      help='7 character binary string for modules to prune (base_conv|top_conv|rpn_conv|top_fc|rpn_fc|downsample|bn)',
                      default="0000000", type=str)

# set training session
  parser.add_argument('--s', dest='session',
                      help='training session',
                      default=1, type=int)

# resume trained model
  parser.add_argument('--r', dest='resume',
                      help='resume checkpoint or not',
                      default=False, type=bool)
  parser.add_argument('--checksession', dest='checksession',
                      help='checksession to load model',
                      default=1, type=int)
  parser.add_argument('--checkepoch', dest='checkepoch',
                      help='checkepoch to load model',
                      default=1, type=int)
  parser.add_argument('--checkpoint', dest='checkpoint',
                      help='checkpoint to load model',
                      default=0, type=int)
# log and display
  parser.add_argument('--use_tfb', dest='use_tfboard',
                      help='whether use tensorboard',
                      action='store_true')

  args = parser.parse_args()
  return args


class sampler(Sampler):
  def __init__(self, train_size, batch_size):
    self.num_data = train_size
    self.num_per_batch = int(train_size / batch_size)
    self.batch_size = batch_size
    self.range = torch.arange(0,batch_size).view(1, batch_size).long()
    self.leftover_flag = False
    if train_size % batch_size:
      self.leftover = torch.arange(self.num_per_batch*batch_size, train_size).long()
      self.leftover_flag = True

  def __iter__(self):
    rand_num = torch.randperm(self.num_per_batch).view(-1,1) * self.batch_size
    self.rand_num = rand_num.expand(self.num_per_batch, self.batch_size) + self.range

    self.rand_num_view = self.rand_num.view(-1)

    if self.leftover_flag:
      self.rand_num_view = torch.cat((self.rand_num_view, self.leftover),0)

    return iter(self.rand_num_view)

  def __len__(self):
    return self.num_data

if __name__ == '__main__':

  args = parse_args()

  # print('Called with args:')
  # print(args)

  if args.dataset == "pascal_voc":
      args.imdb_name = "voc_2007_trainval"
      args.imdbval_name = "voc_2007_test"
      args.set_cfgs = ['ANCHOR_SCALES', '[8, 16, 32]', 'ANCHOR_RATIOS', '[0.5,1,2]', 'MAX_NUM_GT_BOXES', '20']
  elif args.dataset == "pascal_voc_0712":
      args.imdb_name = "voc_2007_trainval+voc_2012_trainval"
      args.imdbval_name = "voc_2007_test"
      args.set_cfgs = ['ANCHOR_SCALES', '[8, 16, 32]', 'ANCHOR_RATIOS', '[0.5,1,2]', 'MAX_NUM_GT_BOXES', '20']
  elif args.dataset == "coco":
      args.imdb_name = "coco_2014_train+coco_2014_valminusminival"
      args.imdbval_name = "coco_2014_minival"
      args.set_cfgs = ['ANCHOR_SCALES', '[4, 8, 16, 32]', 'ANCHOR_RATIOS', '[0.5,1,2]', 'MAX_NUM_GT_BOXES', '50']
  elif args.dataset == "imagenet":
      args.imdb_name = "imagenet_train"
      args.imdbval_name = "imagenet_val"
      args.set_cfgs = ['ANCHOR_SCALES', '[4, 8, 16, 32]', 'ANCHOR_RATIOS', '[0.5,1,2]', 'MAX_NUM_GT_BOXES', '30']
  elif args.dataset == "vg":
      # train sizes: train, smalltrain, minitrain
      # train scale: ['150-50-20', '150-50-50', '500-150-80', '750-250-150', '1750-700-450', '1600-400-20']
      args.imdb_name = "vg_150-50-50_minitrain"
      args.imdbval_name = "vg_150-50-50_minival"
      args.set_cfgs = ['ANCHOR_SCALES', '[4, 8, 16, 32]', 'ANCHOR_RATIOS', '[0.5,1,2]', 'MAX_NUM_GT_BOXES', '50']

  args.cfg_file = "cfgs/{}_ls.yml".format(args.net) if args.large_scale else "cfgs/{}.yml".format(args.net)

  if args.cfg_file is not None:
    cfg_from_file(args.cfg_file)
  if args.set_cfgs is not None:
    cfg_from_list(args.set_cfgs)

  # print('Using config:')
  # pprint.pprint(cfg)
  np.random.seed(args.random_seed)
  torch.manual_seed(args.random_seed)

  #torch.backends.cudnn.benchmark = True
  if torch.cuda.is_available() and not args.cuda:
    print("WARNING: You have a CUDA device, so you should probably run with --cuda")

  run_lth = args.num_rounds>1

  if run_lth:
    if args.keep_percentage == 1.0:
      raise Exception('LTH pruning enabled but keep percentage is 100%')
    if args.module_list == '0000000':
      raise Exception('LTH pruning enabled but no modules set to prune mode')
  else:
    if args.keep_percentage != 1.0:
      raise Exception('LTH pruning disabled but keep percentage not 100%')
    if args.module_list != '0000000':
      raise Exception('LTH pruning disabled but some module set to prune mode')

  # train set
  # -- Note: Use validation set and disable the flipped to enable faster loading.
  cfg.TRAIN.USE_FLIPPED = True
  cfg.USE_GPU_NMS = args.cuda
  imdb, roidb, ratio_list, ratio_index = combined_roidb(args.imdb_name)
  train_size = len(roidb)

  print('{:d} roidb entries'.format(len(roidb)))

  if args.pretrained_path:
    pretrain_string = "_"+args.pretrained_path.split("/")[-2]+"_"+(args.pretrained_path.split("/")[-1]).split(".")[0]
  else:
    raise Exception('Pretrain model path not specified!')
    
  output_dir = args.save_dir + "/" + args.net + "/" + args.dataset
  output_dir += f"_bs{args.batch_size}_lr{args.lr}_lrds{args.lr_decay_step}_kp{args.keep_percentage}_"+\
                f"nr{args.num_rounds}_lri{args.late_reset_iter}_lrw{args.lr_warmup}_rs{args.random_seed}_ml{args.module_list}"+\
                f"{pretrain_string}"
  checkpoint = int(10022/args.batch_size)-1
  # if os.path.exists(os.path.join(output_dir,f'faster_rcnn_{args.max_epochs}_{checkpoint}.pth')):
  #   quit()
  output_dir_round1 = output_dir
  # output_dir_round1 = args.save_dir + "/" + args.net + "/" + args.dataset
  # output_dir_round1 += f"_bs{args.batch_size}_lr{args.lr}_lrds{args.lr_decay_step}_"+\
  #                      f"lrw{args.lr_warmup}_rs{args.random_seed}"


  sampler_batch = sampler(train_size, args.batch_size)

  dataset = roibatchLoader(roidb, ratio_list, ratio_index, args.batch_size, \
                           imdb.num_classes, training=True)

  dataloader = torch.utils.data.DataLoader(dataset, batch_size=args.batch_size,
                            sampler=sampler_batch, num_workers=args.num_workers)

  # initilize the tensor holder here.
  im_data = torch.FloatTensor(1)
  im_info = torch.FloatTensor(1)
  num_boxes = torch.LongTensor(1)
  gt_boxes = torch.FloatTensor(1)

  # ship to cuda
  if args.cuda:
    im_data = im_data.cuda()
    im_info = im_info.cuda()
    num_boxes = num_boxes.cuda()
    gt_boxes = gt_boxes.cuda()

  # make variable
  im_data = Variable(im_data)
  im_info = Variable(im_info)
  num_boxes = Variable(num_boxes)
  gt_boxes = Variable(gt_boxes)

  if args.cuda:
    cfg.CUDA = True

  # initilize the network here.
  if args.net == 'vgg16':
    fasterRCNN = vgg16(imdb.classes, pretrained=True, class_agnostic=args.class_agnostic)
  elif args.net == 'res101':
    fasterRCNN = resnet(imdb.classes, 101, pretrained=True, class_agnostic=args.class_agnostic)
  elif args.net == 'res50':
    fasterRCNN = resnet(imdb.classes, 50, pretrained=True, class_agnostic=args.class_agnostic)
  elif args.net == 'res18':
    fasterRCNN = resnet(imdb.classes, 18, pretrained=True, class_agnostic=args.class_agnostic, \
                        pretrained_path=args.pretrained_path)
  elif args.net == 'res152':
    fasterRCNN = resnet(imdb.classes, 152, pretrained=True, class_agnostic=args.class_agnostic)
  else:
    print("network is not defined")
    pdb.set_trace()

  fasterRCNN.create_architecture()

  if args.cuda:
    fasterRCNN.cuda()

  if args.mGPUs:
    fasterRCNN = nn.DataParallel(fasterRCNN)

  in_mask = {}
  n_zeros = 0
  in_n_mask_dims = 0
  for name, param in fasterRCNN.named_parameters():
      # if 'weight' in name and ('conv' in name or 'Conv' in name):
      if 'weight' in name and 'conv' not in name:
          in_mask[name] = torch.ones_like(param)
          in_mask[name][param==0] = 0
          in_n_mask_dims += param.numel()
          n_zeros += torch.sum(param==0).item()
  print('Percentage zeros in net: {:.4f}'.format(n_zeros/in_n_mask_dims*100))

  if os.path.exists(os.path.join(output_dir,f'faster_rcnn_{args.session}_{args.num_rounds}_{args.max_epochs}_{checkpoint}.pth')):
    print('Skipping training as {} exists'\
          .format(os.path.join(output_dir,f'faster_rcnn_{args.session}_{args.num_rounds}_{args.max_epochs}_{checkpoint}.pth')))
    quit()
    
  if run_lth:
    lth_pruner = lth(fasterRCNN, args.keep_percentage, args.num_rounds, args.late_reset_iter, args.module_list)
    n_total = 0
    n_params = 0
    for name, param in fasterRCNN.named_parameters():
      n_total += param.numel()
      if lth_pruner.check_modules(name):
        n_params += param.numel()
    print(f'Param percentage {n_params/n_total*100}%')

  # print(fasterRCNN)
  # count = {'base_conv':0, 'base_downsample':0, 'base_bn':0, 'top_conv':0, 'top_downsample':0, 'top_bn':0, 'top_fc':0, 'rpn_conv':0, 'rpn_fc':0}
  # total = 0
  # for name, param in fasterRCNN.named_parameters():
  #   total += param.numel()
  #   print(name,lth_pruner.check_modules(name))
  #   if 'base' in name:
  #     if 'conv' in name and 'weight' in name:
  #       count['base_conv'] += param.numel()
  #     if 'downsample' in name and 'weight' in name:
  #       count['base_downsample'] += param.numel()
  #     if 'bn' in name and 'weight' in name:
  #       count['base_bn'] += param.numel()
  #   if 'top' in name:
  #     if 'conv' in name and 'weight' in name:
  #       count['top_conv'] += param.numel()
  #     if 'downsample' in name and 'weight' in name:
  #       count['top_downsample'] += param.numel()
  #     if 'bn' in name and 'weight' in name:
  #       count['top_bn'] += param.numel()
  #   if 'RPN' not in name and ('cls' in name or 'bbox' in name) and 'weight' in name:
  #     count['top_fc'] += param.numel()
  #   if 'RPN' in name:
  #     if 'Conv' in name and 'weight' in name:
  #       count['rpn_conv'] += param.numel()
  #     if ('cls' in name or 'bbox' in name) and 'weight' in name:
  #       count['rpn_fc'] += param.numel()


  # print({k:round(v/total*100,4) for k,v in count.items()})
  # exit()

  ts = time.time()
  for cur_round in range(args.num_rounds):
    start_round = time.time() 
    
    lr = cfg.TRAIN.LEARNING_RATE
    lr = args.lr
    #tr_momentum = cfg.TRAIN.MOMENTUM
    #tr_momentum = args.momentum

    if cur_round == 0:
      cur_output_dir = output_dir_round1
    else:
      cur_output_dir = output_dir

    if not os.path.exists(cur_output_dir):
      os.makedirs(cur_output_dir)
    params = []

    for key, value in dict(fasterRCNN.named_parameters()).items():
      if value.requires_grad:
        if 'bias' in key:
          params += [{'params':[value],'lr':lr*(cfg.TRAIN.DOUBLE_BIAS + 1), \
                  'weight_decay': cfg.TRAIN.BIAS_DECAY and cfg.TRAIN.WEIGHT_DECAY or 0}]
        else:
          params += [{'params':[value],'lr':lr, 'weight_decay': cfg.TRAIN.WEIGHT_DECAY}]

    if args.optimizer == "adam" and cur_round ==0:
      lr = lr * 0.1
      optimizer = torch.optim.Adam(params)

    elif args.optimizer == "sgd":
      optimizer = torch.optim.SGD(params, momentum=cfg.TRAIN.MOMENTUM)

    if args.resume and cur_round == 0:
      load_name = os.path.join(cur_output_dir,
        'faster_rcnn_{}_{}_{}.pth'.format(args.checksession, args.checkepoch, args.checkpoint))
      print("loading checkpoint %s" % (load_name))
      checkpoint = torch.load(load_name)
      args.session = checkpoint['session']
      args.start_epoch = checkpoint['epoch']
      fasterRCNN.load_state_dict(checkpoint['model'])
      optimizer.load_state_dict(checkpoint['optimizer'])
      lr = optimizer.param_groups[0]['lr']
      if 'pooling_mode' in checkpoint.keys():
        cfg.POOLING_MODE = checkpoint['pooling_mode']
      print("loaded checkpoint %s" % (load_name))


    iters_per_epoch = int(train_size / args.batch_size)

    if run_lth:
      if lth_pruner.late_reset_iter >= iters_per_epoch:
        raise Exception('Late reset iteration too high! Max value: {}'.format(iters_per_epoch))

    if args.lr_warmup >= iters_per_epoch:
      raise Exception('Warmup period too high! Max value: {}'.format(iters_per_epoch))

    if args.use_tfboard:
      from tensorboardX import SummaryWriter
      logger = SummaryWriter("logs")

    if cur_round>=1:
      if lth_pruner.init_state_dict is None or lth_pruner.init_opt_state_dict is None:
        init_checkpoint = torch.load(os.path.join(output_dir_round1,f'faster_rcnn_init_checkpoint_{args.late_reset_iter}.pth'))
        if args.mGPUs:
          lth_pruner.init_state_dict = {"module."+k:v for k,v in init_checkpoint["model"].items()}
        else:
          lth_pruner.init_state_dict = init_checkpoint["model"]
        lth_pruner.init_opt_state_dict = init_checkpoint["optimizer"]

      fasterRCNN.load_state_dict(lth_pruner.init_state_dict)
      optimizer.load_state_dict(lth_pruner.init_opt_state_dict)
      fasterRCNN = lth_pruner.generate_new_mask(fasterRCNN, cur_round)
      fasterRCNN = lth_pruner.apply_mask(fasterRCNN)

    # print(os.path.join(cur_output_dir,f'faster_rcnn_{args.session}_{cur_round+1}_{args.max_epochs}_{checkpoint}.pth'))
    # print(os.path.join(output_dir_round1,f'faster_rcnn_init_checkpoint_{args.late_reset_iter}.pth'))
    # if run_lth and os.path.exists(os.path.join(cur_output_dir,f'faster_rcnn_{args.session}_{cur_round+1}_{args.max_epochs}_{checkpoint}.pth')) and \
    # os.path.exists(os.path.join(output_dir_round1,f'faster_rcnn_init_checkpoint_{args.late_reset_iter}.pth')):  
    #   continue

    for epoch in range(args.start_epoch, args.max_epochs + 1):
      # setting to train mode
      fasterRCNN.train()
      loss_temp = 0
      start = time.time()
      es = time.time()

      if epoch % (args.lr_decay_step + 1) == 0:
          adjust_learning_rate(optimizer, args.lr_decay_gamma)
          lr *= args.lr_decay_gamma

      data_iter = iter(dataloader)
      for step in range(iters_per_epoch):
        if args.lr_warmup > step:
          if epoch==args.start_epoch:
            mult = (step+1)/step if step > 0 else 1/args.lr_warmup
            warmup_learning_rate(optimizer, mult)

        if epoch == args.start_epoch and cur_round == 0 and run_lth and step == lth_pruner.late_reset_iter:
          save_name = os.path.join(output_dir_round1, 'faster_rcnn_init_checkpoint_{}.pth'.format(args.late_reset_iter))
          save_ckpt_dict = {
              'session': args.session,
              'epoch': epoch + 1,
              'model': fasterRCNN.module.state_dict() if args.mGPUs else fasterRCNN.state_dict(),
              'optimizer': optimizer.state_dict(),
              'pooling_mode': cfg.POOLING_MODE,
              'class_agnostic': args.class_agnostic,
            }
          if run_lth:
            save_ckpt_dict['lth_pruner'] = lth_pruner
            lth_pruner.init_state_dict = fasterRCNN.state_dict()
            lth_pruner.init_opt_state_dict = optimizer.state_dict()

          save_checkpoint(save_ckpt_dict, save_name)
          print('save model: {}'.format(save_name))


        data = next(data_iter)
        with torch.no_grad():
                im_data.resize_(data[0].size()).copy_(data[0])
                im_info.resize_(data[1].size()).copy_(data[1])
                gt_boxes.resize_(data[2].size()).copy_(data[2])
                num_boxes.resize_(data[3].size()).copy_(data[3])

        fasterRCNN.zero_grad()
        rois, cls_prob, bbox_pred, \
        rpn_loss_cls, rpn_loss_box, \
        RCNN_loss_cls, RCNN_loss_bbox, \
        rois_label = fasterRCNN(im_data, im_info, gt_boxes, num_boxes)

        loss = rpn_loss_cls.mean() + rpn_loss_box.mean() \
             + RCNN_loss_cls.mean() + RCNN_loss_bbox.mean()
        loss_temp += loss.item()

        # backward
        optimizer.zero_grad()
        loss.backward()
        if args.net == "vgg16":
            clip_gradient(fasterRCNN, 10.)
        # elif args.net == "res50":
        #     clip_gradient(fasterRCNN, 10.)
        optimizer.step()
        if run_lth:
          state_dict = fasterRCNN.state_dict()
          for name in state_dict:
              if name in lth_pruner.mask:
                  state_dict[name] *= lth_pruner.mask[name]

        state_dict = fasterRCNN.state_dict()
        for name in state_dict:
            if name in in_mask:
                state_dict[name] *= in_mask[name]


        if step % args.disp_interval == 0:
          end = time.time()
          if step > 0:
            loss_temp /= (args.disp_interval + 1)

          if args.mGPUs:
            loss_rpn_cls = rpn_loss_cls.mean().item()
            loss_rpn_box = rpn_loss_box.mean().item()
            loss_rcnn_cls = RCNN_loss_cls.mean().item()
            loss_rcnn_box = RCNN_loss_bbox.mean().item()
            fg_cnt = torch.sum(rois_label.data.ne(0))
            bg_cnt = rois_label.data.numel() - fg_cnt
          else:
            loss_rpn_cls = rpn_loss_cls.item()
            loss_rpn_box = rpn_loss_box.item()
            loss_rcnn_cls = RCNN_loss_cls.item()
            loss_rcnn_box = RCNN_loss_bbox.item()
            fg_cnt = torch.sum(rois_label.data.ne(0))
            bg_cnt = rois_label.data.numel() - fg_cnt

          print("[round %d][session %d][epoch %2d][iter %4d/%4d] loss: %.4f, lr: %.2e" \
                                  % (cur_round+1,args.session, epoch, step, iters_per_epoch, loss_temp, lr))
          print("\t\t\tfg/bg=(%d/%d), time cost: %f" % (fg_cnt, bg_cnt, end-start))
          print("\t\t\trpn_cls: %.4f, rpn_box: %.4f, rcnn_cls: %.4f, rcnn_box %.4f" \
                        % (loss_rpn_cls, loss_rpn_box, loss_rcnn_cls, loss_rcnn_box))
          if args.use_tfboard:
            info = {
              'loss': loss_temp,
              'loss_rpn_cls': loss_rpn_cls,
              'loss_rpn_box': loss_rpn_box,
              'loss_rcnn_cls': loss_rcnn_cls,
              'loss_rcnn_box': loss_rcnn_box
            }
            logger.add_scalars("logs_s_{}/losses".format(args.session), info, (epoch - 1) * iters_per_epoch + step)

          loss_temp = 0
          start = time.time()
      ee = time.time()

      if epoch == args.max_epochs:
        save_name = os.path.join(cur_output_dir, 'faster_rcnn_{}_{}_{}_{}.pth'.format(args.session, cur_round+1, epoch, step))
        save_ckpt_dict = {
            'session': args.session,
            'epoch': epoch + 1,
            'model': fasterRCNN.module.state_dict() if args.mGPUs else fasterRCNN.state_dict(),
            'optimizer': optimizer.state_dict(),
            'pooling_mode': cfg.POOLING_MODE,
            'class_agnostic': args.class_agnostic,
          }
        if run_lth:
          save_ckpt_dict['lth_pruner'] = lth_pruner

        save_checkpoint(save_ckpt_dict, save_name)
        print('save model: {}'.format(save_name))
      print('time taken : {:.4f} s'.format(ee-es))

    time_elapsed = time.time() - start_round
    print_str = lth_pruner.get_lth_stats()+'\nRound {} training complete in {:.0f}m {:.0f}s'.format(cur_round+1,time_elapsed // 60, time_elapsed % 60)
    print(print_str)
      # send_message(print_str, "vulcan_1", "01")

  print('Total training time: {:.4f} s'.format(time.time()-ts))
  if args.use_tfboard:
    logger.close()
