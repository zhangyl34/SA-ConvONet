import torch
import torch.optim as optim
import os
import shutil
import argparse
from tqdm import tqdm
import time, datetime
from collections import defaultdict
import pandas as pd
from src import config
from src import conv_onet
from tensorboardX import SummaryWriter

# load config
cfg = config.load_config('configs/demo_syn_room.yaml')

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")  # 'cuda'
generation_dir = cfg['training']['out_dir']  # out
input_type = cfg['data']['input_type']       # pointcloud

# Dataset
dataset = config.get_dataset('test', cfg, return_idx=True)

# Model
model = conv_onet.config.get_model(cfg, device=device, dataset=dataset)

# Generator
generator = conv_onet.config.get_generator(model, cfg, device=device)

# Loader
test_loader = torch.utils.data.DataLoader(
    dataset, batch_size=1, num_workers=0, shuffle=False)

# 只有一个 model 所以只循环一次
for it, data in enumerate(tqdm(test_loader)):
    # out/
    mesh_dir = os.path.join(generation_dir, 'meshes')
    log_dir = os.path.join(generation_dir, 'log')

    # Create directories if necessary
    if not os.path.exists(mesh_dir):
        os.makedirs(mesh_dir)
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    logger = SummaryWriter(log_dir)

    # load model
    filename = os.path.join(cfg['test']['model_path'], cfg['test']['model_file'])
    state_dict = torch.load(filename).get('model')

    def generate_mesh_func(iter, is_final=False, th=0.2, suffix='th0.2'):
        # Generate
        generator.threshold = th
        model.eval()
        out = generator.generate_mesh(data)
        mesh, _ = out

        # Write output
        if not is_final:
            mesh_out_file = os.path.join(mesh_dir, 'iter%d_%s.off' % (iter, suffix))
        else:
            mesh_out_file = os.path.join(mesh_dir, 'final_%s.off' % (suffix))
        mesh.export(mesh_out_file)
    
    # Intialize training using pretrained model, and then optimize network parameters for each observed input.
    lr = cfg['test_optim']['learning_rate']          # 0.00003
    lr_decay = cfg['test_optim']['decay_rate']       # 0.3
    n_iter = cfg['test_optim']['n_iter']             # 720
    n_step = cfg['test_optim']['n_step']             # 300
    batch_size = cfg['test_optim']['batch_size']     # 6
    npoints1 = cfg['test_optim']['npoints_surf']     # 1536
    npoints2 = cfg['test_optim']['npoints_nonsurf']  # 512
    sigma = cfg['test_optim']['sigma']               # 0.1
    thres_list = cfg['test_optim']['threshold']      # [0.15, 0.2, 0.25] 三个阈值用来判读是否为 surface
    optimizer = optim.Adam(model.parameters(), lr=lr)
    trainer = conv_onet.training.Trainer(
        model, optimizer, device=device,
        input_type=input_type, threshold=thres_list[1],
        eval_sample=cfg['training']['eval_sample'],
    )

    # Generate results before test-time optimization (results of pretrained ConvONet)
    th = thres_list[1]
    generate_mesh_func(0, th=th, suffix=f"th{th}")

    # range(0, 720)
    for iter in range(0, n_iter):
        # (data, state_dict, 6, 1536, 512, 0.1)
        loss = trainer.sign_agnostic_optim_step(data, state_dict, batch_size, npoints1, npoints2, sigma)
        
        logger.add_scalar('test_optim/loss', loss, iter)
        print('[It %02d] iter_ft=%03d, loss=%.4f' % (it, iter, loss))
        
        if (iter + 1) % n_step == 0:
            lr = lr * lr_decay
            for g in optimizer.param_groups:
                g['lr'] = lr
                trainer = conv_onet.training.Trainer(
                    model, optimizer, device=device,
                    input_type=input_type, threshold=thres_list[1],
                    eval_sample=cfg['training']['eval_sample'],
                )
            for th in thres_list:
                generate_mesh_func(iter, th=th, suffix=f"th{th}")

    for th in thres_list:
        generate_mesh_func(n_iter, is_final=True, th=th, suffix=f"th{th}")

print('optimization finish.')