Retinanet的一切

### 设计
**关于总体结构**
1. 骨架网络: resnet50
2. neck: fpn
3. head:


**关于如何定义训练target**
0. 核心：


**关于anchor尺寸和生成机制**
0. 核心：


**关于输入图片尺寸**
1. 训练时：retinanet对输入图片的处理方式：先等比例放大或者缩减到1333,800的方框内，然后padding成统一大小后堆叠成batch
2. 测试时：同样是把图片缩放到1333,800的尺寸内，这对一些小物体也是放大了进行检测，有利于小物体检测。
        - 优点：由于retinanet的要求尺寸比较大，大部分时候都是把图片放大了，有利于对数据集的训练，同时由于是等比例缩放，也不会造成比例失调。
        - 缺点：图片尺寸较大，在检测时可能比较耗时


### 调试

**关于下采样和上采样**
0. 核心：


**关于设置三种损失函数的取值权重问题**
0. 核心：

**关于设置获取target时正负样本的分割阈值**
0. 核心：


**关于设置负样本挖掘时正负样本比例的问题**
0. 核心：


