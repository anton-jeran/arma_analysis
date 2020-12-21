import argparse
from main import *

def get_args():
	# Arguement Parser

	model_names = sorted(name for name in models.__dict__
		if name.islower() and not name.startswith("__")
		and callable(models.__dict__[name]))

	parser = argparse.ArgumentParser(description="Add more options if necessary")

	parser.add_argument('-a', '--arch', metavar='ARCH', default='resnet50',
						choices=model_names,
						help='model architecture: ' +
							' | '.join(model_names) +
							' (default: resnet50)')

	#Options related to distributed training.
	parser.add_argument('--world-size', default=-1, type=int,
						help='number of nodes for distributed training')
	parser.add_argument('--rank', default=-1, type=int,
						help='node rank for distributed training')
	parser.add_argument('--dist-url', default='tcp://localhost:10004', type=str,
						help='url used to set up distributed training')
	parser.add_argument('--dist-backend', default='nccl', type=str,
						help='distributed backend')
	parser.add_argument('--seed', default=None, type=int,
						help='seed for initializing training. ')
	parser.add_argument('--gpu', default=None, type=int,
						help='GPU id to use.')
	parser.add_argument('--multiprocessing-distributed', action='store_true',
						help='Use multi-processing distributed training to launch '
							 'N processes per node, which has N GPUs. This is the '
							 'fastest way to use PyTorch for either single node or '
							 'multi node data parallel training')


	parser.add_argument("--batch_size", default=12, type=int)	
	parser.add_argument('--debug', action='store_true', help='debug')

	# optimization
	parser.add_argument('--lr', type=float, default=0.03, help='learning rate')
	parser.add_argument('--lr-decay-epochs', type=int, default=[120, 160, 200], nargs='+',
						help='where to decay lr, can be a list')
	parser.add_argument('--lr-decay-rate', type=float, default=0.1, help='decay rate for learning rate')
	parser.add_argument('--weight-decay', type=float, default=5e-4, help='weight decay')
	parser.add_argument('--momentum', type=float, default=0.9, help='momentum for SGD')
	parser.add_argument('--workers', type=int, default=16, help='num of workers to use')
	parser.add_argument('--epochs', type=int, default=200, help='number of training epochs')
	parser.add_argument('--start_epoch', type=int, default=0, help='number of training epochs')

	parser.add_argument("--local_rank", type=int)
	parser.add_argument('--cos', action='store_true',
					help='use cosine lr schedule')
	parser.add_argument('--schedule',default=None,nargs='+',
					help='use custom schedule, where lr is decreased by 0.1 at those schedules.')


	# resume, save,folders stuff
	parser.add_argument('--data',default='data/',type=str,help='Data directory')
	parser.add_argument('--resume', default='', type=str, metavar='PATH',
					   help='path to latest checkpoint (default: none)')
	parser.add_argument('--exp_name', type=str, default='exp',
						help='experiment name, used to store everything related to this experiment')
	parser.add_argument('--save_freq',default=None,help='Use this to set value for periodic \
		saving of all ckpts.',type=int)


	parser.add_argument('--n_classes',default=21,type=int,help='number of classes')
	parser.add_argument('--no_pre_train',dest='pre_train',action='store_true')
	parser.set_defaults(no_pre_train=False)
	parser.add_argument('--model_type',default='fcn_resnet18',help='model to load and run')

	args = parser.parse_args()

	return args


