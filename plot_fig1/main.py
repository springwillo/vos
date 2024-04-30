import torch
import numpy as np
import matplotlib.pyplot as plt
from torch.utils.data import DataLoader
from torch import nn
import torch.optim as optim
from utils import generate_data, remove_2d_outlier
from dataset import MyDataset, MyDataset_outlier
from model import MLP
import torch.optim as optim
import argparse
import random
import matplotlib as mpl

# 创建命令行解析器
parser = argparse.ArgumentParser(description='Tunes a CIFAR Classifier with OE',
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)

parser.add_argument('--eval_model',  action='store_true', help='whether to train the model or directly inference using the checkpoint')
parser.add_argument('--train_scheme', type=str, default='ce', help='ce|vos, here ce denotes the original training scheme, vos denotes training vos')
parser.add_argument('--device', type=str, default='cpu', help='cpu|cuda')
parser.add_argument('--train_epoch', type=int, default=3000)

args = parser.parse_args()

# 设置模型保存路径
save_path = f'./results/{args.train_scheme}.pth' 
gap = 12  #数据点之间的间隔
warmup_num=20 #学习率 warmup 的 epoch 数
learning_rate =  0.5 # 初始学习率
variance =0.25  # 数据点分布的方差
data_num = 500 # 每个类别的数据点数量
class_num =3 # 类别数量
input_dim = 2 # 输入维度
positive_limit = 16  # 数据点的正负限制

# 每个类别的训练数据数量
train_data_num  =  [data_num for _ in range(class_num)] 
batch_size = sum(train_data_num)  # 批处理大小

# 数据点的中心位置
gap_list = [0, - gap/2, gap/2 ]
y_center = [gap /np.sqrt(3), - gap /np.sqrt(3)/2, - gap /np.sqrt(3)/2]

# 生成训练数据
train_data, train_label = generate_data(batch_size,input_dim, class_num, gap_list,y_center, variance, train_data_num )

# 创建模型
model_vanilia = MLP(input_dim, class_num)

# 如果是评估模式，则加载模型
if args.eval_model:
    model_vanilia.load_state_dict(torch.load(save_path))
    model_vanilia.to(args.device)
else:
     # 如果是训练模式，则设置优化器和数据加载器
    sigmoid = torch.nn.Sigmoid() # 创建sigmoid激活函数，用于后续计算
    train_data_normal = train_data / positive_limit # 对训练数据进行归一化
    # 如果采用vos训练方案
    if args.train_scheme == 'vos':
        optimizer_vanilia = optim.SGD(model_vanilia.parameters() ,lr=learning_rate,weight_decay=0)
        # 生成异常值数据
        outlier = np.random.random((class_num* data_num,2)) * 2 * positive_limit  - positive_limit  # 随机生成异常值数据
        p_outlier =  0.000000000000000000000005   #  0.000000000000000000000005  # 设置异常值的概率阈值
        outlier = remove_2d_outlier(outlier, p_outlier, gap_list ,y_center , [variance for _ in range(class_num)], positive_limit) # 过滤掉不符合条件的异常值
        # 创建异常值标签
        outlier_label = torch.cat((torch.ones(len(outlier)), torch.zeros(len(outlier))), 0)
        outlier_label = torch.unsqueeze(outlier_label, 1).float() 
        outlier_train_data = (torch.from_numpy(outlier)).float()
        outlier_train_data = outlier_train_data / positive_limit
        outlier_dataset = MyDataset(outlier_train_data, outlier_label[len(outlier_label)//2:,:])
        outlier_train_loader = DataLoader(outlier_dataset, batch_size=batch_size,shuffle=True)
        outlier_criterion = torch.nn.BCELoss(size_average=True)
        
        dataset = MyDataset_outlier(train_data_normal, train_label, outlier_label[:len(outlier_label)//2,:])
        train_loader = DataLoader(dataset, batch_size=batch_size,shuffle=True)

    else:
        optimizer_vanilia = optim.SGD(model_vanilia.parameters(),lr=learning_rate,weight_decay=0.0005) 
        dataset = MyDataset(train_data_normal, train_label)
        train_loader = DataLoader(dataset, batch_size=batch_size,shuffle=True)

    loss_function = nn.CrossEntropyLoss()
    model_vanilia.to(args.device)



    lambda_va = lambda epoch: (0.9*epoch / warmup_num +0.1) if epoch < warmup_num else 1 
    scheduler_vanilia = optim.lr_scheduler.LambdaLR(optimizer_vanilia, lr_lambda=lambda_va)

    for epoch in range(args.train_epoch): 

        if args.train_scheme == 'vos':
            for step, data in enumerate(zip(train_loader, outlier_train_loader), start=0):  
                inputs, labels, in_outlier_labels = data[0][0],  data[0][1],  data[0][2]	
                outlier_inputs, outlier_labels = data[1][0],  data[1][1]
                optimizer_vanilia.zero_grad()  

                data_cat = torch.cat((inputs, outlier_inputs), 0)
                label_cat =  torch.cat((in_outlier_labels, outlier_labels* 0), 0)
                outputs_vanilia = model_vanilia(data_cat.to(args.device))
                outlier_energy = torch.logsumexp(outputs_vanilia, dim=1, keepdim=True)  
                pred_outlier = sigmoid(outlier_energy)
                loss_vanilia = loss_function(outputs_vanilia[:len(outputs_vanilia)//2], labels.to(args.device)) 
                loss_ood = outlier_criterion(pred_outlier, label_cat.to(args.device))
                loss = loss_vanilia +  0.1 * loss_ood
                loss.backward()
                optimizer_vanilia.step() 	

        else:
            for data in train_loader:  
                inputs, labels = data[0],  data[1]
                optimizer_vanilia.zero_grad()  
                outputs_vanilia = model_vanilia(inputs.to(args.device))
                loss = loss_function(outputs_vanilia, labels.to(args.device)) 
                loss.backward() 				
                optimizer_vanilia.step() 	

        with torch.no_grad():
            if (epoch+1)%50==0:
                torch.save(model_vanilia.state_dict(), save_path)  
                with torch.no_grad():
                    if args.train_scheme == 'vos':
                        pred_outlier = torch.sign(pred_outlier - 1/2)
                        pred_outlier = (pred_outlier + 1)/2
                        outlier_accuracy = (pred_outlier == label_cat.to(args.device)).sum().item() / label_cat.size(0) 
                        print(epoch+1, 'loss_OOD:', loss_ood.item(),'loss_ce:', loss_vanilia.item(), 'OOD test acc:', outlier_accuracy)
                    else:
                        print(epoch+1, 'loss_ce:', loss.item())
        scheduler_vanilia.step()
    print('Finished Training')
    torch.save(model_vanilia.state_dict(), save_path)

#  Inference and Draw plot

plt.rcParams['figure.figsize'] = (9.5, 8.0) 
plot_num = 50 
range_num = 16 
x = np.linspace(-range_num, range_num, range_num * plot_num)
y = np.linspace(-range_num, range_num, range_num * plot_num)
z = np.zeros (( range_num * plot_num,  range_num * plot_num))
x = np.expand_dims(x, axis=1)
x =x.repeat(range_num * plot_num, axis = 1)  
y = x.T
x_test = np.expand_dims(np.asarray(x).reshape(-1), 1) 
y_test = np.expand_dims(np.asarray(y).reshape(-1), 1) 
test_data_demo = np.concatenate((x_test, y_test), 1)
test_data_demo = torch.from_numpy(test_data_demo).float()
outputs_test = model_vanilia((test_data_demo/positive_limit).to(args.device)) 				 #		
outputs_test = outputs_test.cpu().detach().numpy()

MSP_base = np.sum( np.exp(outputs_test), axis=1)
MSP_base = np.reshape(MSP_base, (-1, len(x)))
Energy_test_vanilia =  - np.log (MSP_base)

scatter_size = 30
alpha_value = 0.1
height_location = 11
font_size= 27 

fig = plt.figure() # 创建一个新的图像窗口。
ax = fig.add_subplot(111) # 在图像中添加一个子图，这里111表示图像应该是1x1网格中的第一个图。
plt.contourf(x,  y, - Energy_test_vanilia, 500  , cmap=mpl.colormaps['Purples'], linestyles = 'dashed') 
# 使用x和y的数据绘制一个等高线图，其中-Energy_test_vanilia定义了等高线的值，500是等高线的数量，
# cmap指定了颜色映射（这里使用紫色调），linestyles指定了等高线的线型为虚线。

plt.text(height_location, -18, 'Low ID score',  fontdict={'family' : 'Times New Roman', 'size'   : font_size})  
cb = plt.colorbar() 
plt.setp(cb.ax.get_yticklabels(), visible=False) # 隐藏颜色条上的y轴刻度标签。
plt.text(height_location, 17, 'High ID score',  fontdict={'family' : 'Times New Roman', 'size'   : font_size}) 
plt.text(height_location, -18, 'Low ID score',  fontdict={'family' : 'Times New Roman', 'size'   : font_size}) 
plt.xticks([])
plt.yticks([])
points_visual = 100
points_visual_index = random.sample(range(0, train_data.size(0)), points_visual) 
plt.scatter(train_data[points_visual_index,0],train_data[points_visual_index,1], s=scatter_size, marker="o", alpha=0.5, edgecolors = 'None', c = 'grey' , label='ID Data') 
plt.rc('font',family='Times New Roman')
plt.legend(scatterpoints=1,fontsize=font_size)  
plt.savefig(f'results/contour_{args.train_scheme}.png',  dpi=200, bbox_inches='tight')
