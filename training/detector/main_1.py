import argparse
import os,csv
import time
import numpy as np
import data
from importlib import import_module
import shutil
from utils import *
import sys
sys.path.append('../')
from split_combine import SplitComb

import torch
from torch.nn import DataParallel
from torch.backends import cudnn
from torch.utils.data import DataLoader
from torch import optim
from torch.autograd import Variable
from config_training import config as config_training

from layers import acc
import warnings
warnings.filterwarnings('ignore')

parser = argparse.ArgumentParser(description='PyTorch DataBowl3 Detector')
parser.add_argument('--model', '-m', metavar='MODEL', default='res18',
                    help='model')
parser.add_argument('-j', '--workers', default=32, type=int, metavar='N',
                    help='number of data loading workers (default: 32)')
parser.add_argument('--epochs', default=100, type=int, metavar='N',
                    help='number of total epochs to run')
parser.add_argument('--start-epoch', default=0, type=int, metavar='N',
                    help='manual epoch number (useful on restarts)')
parser.add_argument('-b', '--batch-size', default=16, type=int,
                    metavar='N', help='mini-batch size (default: 16)')
parser.add_argument('--lr', '--learning-rate', default=0.01, type=float,
                    metavar='LR', help='initial learning rate')
parser.add_argument('--momentum', default=0.9, type=float, metavar='M',
                    help='momentum')
parser.add_argument('--weight-decay', '--wd', default=1e-4, type=float,
                    metavar='W', help='weight decay (default: 1e-4)')
parser.add_argument('--save-freq', default='1', type=int, metavar='S',
                    help='save frequency')
parser.add_argument('--resume', default='', type=str, metavar='PATH',
                    help='path to latest checkpoint (default: none)')
parser.add_argument('--save-dir', default='res18', type=str, metavar='SAVE',
                    help='directory to save checkpoint (default: none)')
parser.add_argument('--test', default=0, type=int, metavar='TEST',
                    help='1 do test evaluation, 0 not')
parser.add_argument('--split', default=8, type=int, metavar='SPLIT',
                    help='In the test phase, split the image to 8 parts')
parser.add_argument('--gpu', default='all', type=str, metavar='N',
                    help='use gpu')
parser.add_argument('--n_test', default=3, type=int, metavar='N',
                    help='number of gpu for test')

def main():
    global args
    args = parser.parse_args()
    
    
    torch.manual_seed(0)
    torch.cuda.set_device(0)

    model = import_module(args.model)
    config, net, loss, get_pbb = model.get_model()

    #model_path = '/data/wzeng/DSB_3/training/detector/results/res18/026.ckpt'
    #net.load_state_dict(torch.load(model_path)['state_dict'])
    #print('loading model from ' + model_path)
    #model_dict = net.state_dict()
    #pretrained_dict = torch.load("/data/wzeng/DSB_3/model/detector.ckpt")['state_dict']
    # 1. filter out unnecessary keys
    #pretrained_dict = {k: v for k, v in model_dict.items() if k in pretrained_dict}
    # 2. overwrite entries in the existing state dict
    #model_dict.update(pretrained_dict)
    # 3. load the new state dict
    #net.load_state_dict(model_dict)

    start_epoch = args.start_epoch
    save_dir = args.save_dir
    #args.resume = '/data/wzeng/DSB_3/training/detector/results/res18_/070.ckpt'    
    if args.resume:
        print('start resume')
        print('loading model from ' + args.resume)
        checkpoint = torch.load(args.resume)
        if start_epoch == 0:
            start_epoch = checkpoint['epoch'] + 1
        if not save_dir:
            save_dir = checkpoint['save_dir']
        else:
            save_dir = os.path.join('results',save_dir)
        net.load_state_dict(checkpoint['state_dict'])
        print('resume end')
    else:
        if start_epoch == 0:
            start_epoch = 1
        if not save_dir:
            exp_id = time.strftime('%Y%m%d-%H%M%S', time.localtime())
            save_dir = os.path.join('results', args.model + '-' + exp_id)
        else:
            save_dir = os.path.join('results',save_dir)
    
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)
    logfile = os.path.join(save_dir,'log')
    if args.test!=1:
        sys.stdout = Logger(logfile)
        pyfiles = [f for f in os.listdir('./') if f.endswith('.py')]
        for f in pyfiles:
            shutil.copy(f,os.path.join(save_dir,f))
    n_gpu = setgpu(args.gpu)
    args.n_gpu = n_gpu
    net = net.cuda()
    loss = loss.cuda()
    cudnn.benchmark = True
    net = DataParallel(net)
    datadir = config_training['preprocess_result_path']
    
    if args.test == 1:
        margin = 16
        sidelen = 128
        #margin = 32
        #sidelen = 144
#         print('dataloader....')
        split_comber = SplitComb(sidelen,config['max_stride'],config['stride'],margin,config['pad_value'])
        dataset = data.DataBowl3Detector(
            datadir,
            'test.npy',
            config,
            phase='test',
            split_comber=split_comber)
        test_loader = DataLoader(
            dataset,
            batch_size = 1,
            shuffle = False,
            num_workers = args.workers,
            collate_fn = data.collate,
            pin_memory=False)
#         print('start testing.....')
        test(test_loader, net, get_pbb, save_dir,config)
        return

    #net = DataParallel(net)
    
    dataset = data.DataBowl3Detector(
        datadir,
        'train.npy',
        config,
        phase = 'train')
    train_loader = DataLoader(
        dataset,
        batch_size = args.batch_size,
        shuffle = True,
        num_workers = args.workers,
        pin_memory=True)

    dataset = data.DataBowl3Detector(
        datadir,
        'val.npy',
        config,
        phase = 'val')
    val_loader = DataLoader(
        dataset,
        batch_size = args.batch_size,
        shuffle = False,
        num_workers = args.workers,
        pin_memory=True)

    optimizer = torch.optim.SGD(
        net.parameters(),
        args.lr,
        momentum = 0.9,
        weight_decay = args.weight_decay)

    #optimizer = torch.optim.Adam(
    #    net.parameters(),
    #    args.lr,
    #    weight_decay = args.weight_decay)

    
    def get_lr(epoch):
        if epoch <= args.epochs * 0.5:
            lr = args.lr
        elif epoch <= args.epochs * 0.8:
            lr = 0.1 * args.lr
        else:
            lr = 0.01 * args.lr
        return lr
    

    for epoch in range(start_epoch, args.epochs + 1):
        train(train_loader, net, loss, epoch, optimizer, get_lr, args.save_freq, save_dir)
        validate(val_loader, net, loss)

def train(data_loader, net, loss, epoch, optimizer, get_lr, save_freq, save_dir):
    start_time = time.time()
    
    net.train()
    lr = get_lr(epoch)
    for param_group in optimizer.param_groups:
        param_group['lr'] = lr

    metrics = []
    for i, (data, target, coord) in enumerate(data_loader):
        data = Variable(data.cuda())
        target = Variable(target.cuda())
        coord = Variable(coord.cuda())

        output1,output2,output3,output4 = net(data, coord)
        loss_output = loss(output1,output2,output3,output4, target)
        optimizer.zero_grad()
        loss_output[0].backward()
        optimizer.step()

        loss_output[0] = loss_output[0].item()
        metrics.append(loss_output)

    if epoch % args.save_freq == 0:            
        state_dict = net.module.state_dict()
        for key in state_dict.keys():
            state_dict[key] = state_dict[key].cpu()
            
        torch.save({
            'epoch': epoch,
            'save_dir': save_dir,
            'state_dict': state_dict,
            'args': args},
            os.path.join(save_dir, '%03d.ckpt' % epoch))

    end_time = time.time()

    metrics = np.asarray(metrics, np.float32)
    print('Epoch %03d (lr %.5f)' % (epoch, lr))
    print('Train:      tpr %3.2f, tnr %3.2f, total pos %d, total neg %d, time %3.2f' % (
        100.0 * np.sum(metrics[:, 6]) / np.sum(metrics[:, 7]),
        100.0 * np.sum(metrics[:, 8]) / np.sum(metrics[:, 9]),
        np.sum(metrics[:, 7]),
        np.sum(metrics[:, 9]),
        end_time - start_time))
    print('loss %2.4f, classify loss %2.4f, regress loss %2.4f, %2.4f, %2.4f, %2.4f' % (
        np.mean(metrics[:, 0]),
        np.mean(metrics[:, 1]),
        np.mean(metrics[:, 2]),
        np.mean(metrics[:, 3]),
        np.mean(metrics[:, 4]),
        np.mean(metrics[:, 5])))
    print

def validate(data_loader, net, loss):
    start_time = time.time()
    
    net.eval()

    metrics = []
    for i, (data, target, coord) in enumerate(data_loader):
        #with torch.no_grad():
        data = Variable(data.cuda(), volatile = True)
        target = Variable(target.cuda(), volatile = True)
        coord = Variable(coord.cuda(), volatile = True)

        output1,output2,output3,output4 = net(data, coord)
        loss_output = loss(output1,output2,output3,output4, target, train = False)

        loss_output[0] = loss_output[0].item()
        metrics.append(loss_output)    
    end_time = time.time()

    metrics = np.asarray(metrics, np.float32)
    print('Validation: tpr %3.2f, tnr %3.8f, total pos %d, total neg %d, time %3.2f' % (
        100.0 * np.sum(metrics[:, 6]) / np.sum(metrics[:, 7]),
        100.0 * np.sum(metrics[:, 8]) / np.sum(metrics[:, 9]),
        np.sum(metrics[:, 7]),
        np.sum(metrics[:, 9]),
        end_time - start_time))
    print('loss %2.4f, classify loss %2.4f, regress loss %2.4f, %2.4f, %2.4f, %2.4f' % (
        np.mean(metrics[:, 0]),
        np.mean(metrics[:, 1]),
        np.mean(metrics[:, 2]),
        np.mean(metrics[:, 3]),
        np.mean(metrics[:, 4]),
        np.mean(metrics[:, 5])))
    print
    print

def sigmoid(x):
  return 1.0 / (1.0 + np.exp(-x))
def test(data_loader, net, get_pbb, save_dir, config):
    start_time = time.time()
    save_dir = os.path.join(save_dir,'bbox')
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)
    print(save_dir)
    file = open(os.path.join(save_dir,'predict.csv'),'w')
    csv_write = csv.writer(file)
    net.eval()
    namelist = []
    split_comber = data_loader.dataset.split_comber
    for i_name, (data, target, coord, nzhw) in enumerate(data_loader):
        s = time.time()
        target = [np.asarray(t, np.float32) for t in target]
        lbb = target[0]
        nzhw = nzhw[0]
        name = data_loader.dataset.filenames[i_name].split('/')[-1].split('_')[0]
        origin = data_loader.dataset.sample_origin[i_name]
#         name = data_loader.dataset.filenames[i_name].split('-')[0].split('/')[-1].split('_clean')[0]
        data = data[0][0]
        coord = coord[0][0]

        n_per_run = args.n_test
        print(data.size())
        splitlist = list(range(0,len(data)+1,n_per_run))
        if splitlist[-1]!=len(data):
            splitlist.append(len(data))
        outputlist1 = []
        outputlist2 = []
        outputlist3 = []
        outputlist4 = []

        for i in range(len(splitlist)-1):
            with torch.no_grad():
                input = Variable(data[splitlist[i]:splitlist[i+1]]).cuda()
                inputcoord = Variable(coord[splitlist[i]:splitlist[i+1]]).cuda()

            output1,output2,output3,output4 = net(input,inputcoord)
            outputlist1.append(output1.data.cpu().numpy())
            outputlist2.append(output2.data.cpu().numpy())
            outputlist3.append(output3.data.cpu().numpy())
            outputlist4.append(output4.data.cpu().numpy())


        output1 = np.concatenate(outputlist1,0)
        output1 = split_comber.combine(output1,nzhw=nzhw)
        thresh = -3
        _,_,bboxes1 = get_pbb(output1,thresh,ismask=True)
        for bbox in bboxes1:
            csv_write.writerow([name,str(sigmoid(bbox[0])),str(bbox[1]),str(bbox[2]),str(bbox[3]),str(bbox[4]),'1'])

        output2 = np.concatenate(outputlist2,0)
        output2 = split_comber.combine(output2,nzhw=nzhw)
        thresh = -3
        _,_,bboxes2 = get_pbb(output2,thresh,ismask=True)
        for bbox in bboxes2:
            csv_write.writerow([name,str(sigmoid(bbox[0])),str(bbox[1]),str(bbox[2]),str(bbox[3]),str(bbox[4]),'2'])

        output3 = np.concatenate(outputlist3,0)
        output3 = split_comber.combine(output3,nzhw=nzhw)
        thresh = -3
        _,_,bboxes3 = get_pbb(output3,thresh,ismask=True)
        for bbox in bboxes3:
            csv_write.writerow([name,str(sigmoid(bbox[0])),str(bbox[1]),str(bbox[2]),str(bbox[3]),str(bbox[4]),'3'])

        output4 = np.concatenate(outputlist4,0)
        output4 = split_comber.combine(output4,nzhw=nzhw)
        thresh = -3
        _,_,bboxes4 = get_pbb(output4,thresh,ismask=True)
        for bbox in bboxes4:
            csv_write.writerow([name,str(sigmoid(bbox[0])),str(bbox[1]),str(bbox[2]),str(bbox[3]),str(bbox[4]),'4'])

    end_time = time.time()

    file.close()
 
    print('elapsed time is %3.2f seconds' % (end_time - start_time))
    print
    print

def singletest(data,net,config,splitfun,combinefun,n_per_run,margin = 64,isfeat=False):
    z, h, w = data.size(2), data.size(3), data.size(4)
    print(data.size())
    data = splitfun(data,config['max_stride'],margin)
    data = Variable(data.cuda(), volatile = True,requires_grad=False)
    splitlist = range(0,args.split+1,n_per_run)
    outputlist = []
    featurelist = []
    for i in range(len(splitlist)-1):
        if isfeat:
            output,feature = net(data[splitlist[i]:splitlist[i+1]])
            featurelist.append(feature)
        else:
            output = net(data[splitlist[i]:splitlist[i+1]])
        output = output.data.cpu().numpy()
        outputlist.append(output)
        
    output = np.concatenate(outputlist,0)
    output = combinefun(output, z / config['stride'], h / config['stride'], w / config['stride'])
    if isfeat:
        feature = np.concatenate(featurelist,0).transpose([0,2,3,4,1])
        feature = combinefun(feature, z / config['stride'], h / config['stride'], w / config['stride'])
        return output,feature
    else:
        return output
if __name__ == '__main__':
    main()

