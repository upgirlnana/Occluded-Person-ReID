import cv2
import numpy as np
import torch
from torchvision import models, transforms
from torch.autograd import Variable
import torch
import torch.nn as nn
import pickle
import os
import argparse
import matplotlib.pyplot as plt
from matplotlib import cm
from PIL import Image
import glob
from torch.nn import Parameter

class CamExtractor():
    """
        Extracts cam features from the model
    """

    def __init__(self, model, target_layer):
        self.model = model
        self.target_layer = target_layer
        self.gradients = None

    def save_gradient(self, grad):
        self.gradients = grad

    def forward_pass_on_convolutions(self, x):
        """
            Does a forward pass on convolutions, hooks the function at given layer
        """
        conv_output = None
        for module_name, module in self.model._modules.items():
            print(module_name)
            if module_name == 'fc':
                return conv_output, x
            x = module(x)  # Forward
            if module_name == self.target_layer:
                print('True')
                x.register_hook(self.save_gradient)
                conv_output = x  # Save the convolution output on that layer
        return conv_output, x

    def forward_pass(self, x):
        """
            Does a full forward pass on the model
        """
        # Forward pass on the convolutions
        conv_output, x = self.forward_pass_on_convolutions(x)
        x = x.view(x.size(0), -1)  # Flatten
        # Forward pass on the classifier
        x = self.model.fc(x)
        return conv_output, x

class GradCam():
    """
        Produces class activation map
    """

    def __init__(self, model, target_layer):
        self.model = model
        self.model.eval()
        # Define extractor
        self.extractor = CamExtractor(self.model, target_layer)

    def generate_cam(self, input_image, target_index=None):
        # Full forward pass
        # conv_output is the output of convolutions at specified layer
        # model_output is the final output of the model (1, 1000)
        conv_output, model_output = self.extractor.forward_pass(input_image)
        if target_index is None:
            target_index = np.argmax(model_output.data.numpy())
        # Target for backprop
        one_hot_output = torch.FloatTensor(1, model_output.size()[-1]).zero_()
        one_hot_output[0][target_index] = 1
        # Zero grads
        self.model.fc.zero_grad()
        # self.model.classifier.zero_grad()
        # Backward pass with specified target
        model_output.backward(gradient=one_hot_output, retain_graph=True)
        # Get hooked gradients
        guided_gradients = self.extractor.gradients.data.numpy()[0]
        # Get convolution outputs
        target = conv_output.data.numpy()[0]
        # Get weights from gradients
        # Take averages for each gradient
        weights = np.mean(guided_gradients, axis=(1, 2))
        # Create empty numpy array for cam
        cam = np.ones(target.shape[1:], dtype=np.float32)
        # Multiply each weight with its conv output and then, sum
        for i, w in enumerate(weights):
            cam += w * target[i, :, :]
        cam = cv2.resize(cam, (224, 224))
        cam = np.maximum(cam, 0)
        cam = (cam - np.min(cam)) / (np.max(cam) -
                                     np.min(cam))  # Normalize between 0-1
        cam = np.uint8(cam * 255)  # Scale between 0-255 to visualize
        return cam

def save_class_activation_on_image(org_img, activation_map, file_name,layer,save_dir):
    """
        Saves cam activation map and activation map on the original image

    Args:
        org_img (PIL img): Original image
        activation_map (numpy arr): activation map (grayscale) 0-255
        file_name (str): File name of the exported image
    """
    if not os.path.exists(save_dir+file_name):
        os.makedirs(save_dir+file_name)
    path_to_file = os.path.join(save_dir,file_name, file_name + '_'+ layer + '.jpg')
    cv2.imwrite(path_to_file, org_img)
    # Grayscale activation map
    path_to_file = os.path.join(save_dir,file_name, file_name + '_'+ layer +'_Cam_Grayscale.jpg')
    cv2.imwrite(path_to_file, activation_map)
    # Heatmap of activation map
    activation_heatmap = cv2.applyColorMap(activation_map, cv2.COLORMAP_HSV)
    path_to_file = os.path.join(save_dir,file_name, file_name + '_'+ layer + '_Cam_Heatmap.jpg')
    cv2.imwrite(path_to_file, activation_heatmap)
    # Heatmap on picture
    org_img = cv2.resize(org_img, (224, 224))
    img_with_heatmap = np.float32(activation_heatmap) + np.float32(org_img)
    img_with_heatmap = img_with_heatmap / np.max(img_with_heatmap)
    path_to_file = os.path.join(save_dir,file_name, file_name + '_'+ layer + '_Cam_On_Image.jpg')
    cv2.imwrite(path_to_file, np.uint8(255 * img_with_heatmap))

def preprocess_image(cv2im, resize_im=True):
    """
        Processes image for CNNs

    Args:
        PIL_img (PIL_img): Image to process
        resize_im (bool): Resize to 224 or not
    returns:
        im_as_var (Pytorch variable): Variable that contains processed float tensor
    """
    # mean and std list for channels (Imagenet)
    # Resize image
    if resize_im:
        cv2im = cv2.resize(cv2im, (224, 224))
    im_as_arr = np.float32(cv2im)
    im_as_arr = np.ascontiguousarray(im_as_arr[..., ::-1])
    im_as_arr = im_as_arr.transpose(2, 0, 1)
    im_as_ten = torch.from_numpy(im_as_arr).float()
    # Add one more channel to the beginning. Tensor shape = 1,3,224,224
    im_as_ten.unsqueeze_(0)
    # Convert to Pytorch variable
    im_as_var = Variable(im_as_ten, requires_grad=True)
    return im_as_var

def load_state_dict(model, src_state_dict):
  """Copy parameters and buffers from `src_state_dict` into `model` and its
  descendants. The `src_state_dict.keys()` NEED NOT exactly match
  `model.state_dict().keys()`. For dict key mismatch, just
  skip it; for copying error, just output warnings and proceed.

  Arguments:
    model: A torch.nn.Module object.
    src_state_dict (dict): A dict containing parameters and persistent buffers.
  Note:
    This is modified from torch.nn.modules.module.load_state_dict(), to make
    the warnings and errors more detailed.
  """
  dest_state_dict = model.state_dict()
  for name, param in src_state_dict.items():
    name = str.replace(name,"base.","")
    if name not in dest_state_dict:
      continue
    if isinstance(param, Parameter):
       # backwards compatibility for serialized parameters
        param = param.data
    if dest_state_dict[name].size() == param.size():
        dest_state_dict[name].copy_(param)

  src_missing = set(dest_state_dict.keys()) - set(src_state_dict.keys())
  if len(src_missing) > 0:
    print("Keys not found in source state_dict: ")
    for n in src_missing:
      print('\t', n)

  dest_missing = set(src_state_dict.keys()) - set(dest_state_dict.keys())
  if len(dest_missing) > 0:
    print("Keys not found in destination state_dict: ")
    for n in dest_missing:
      print('\t', n)

def load_model(weight_file_path=None, ver=0):

    model = models.resnet50(pretrained=True)
    num_ftrs = model.fc.in_features
    planes = 2048
    model.local_conv = nn.Conv2d(planes, 128, 1)
    model.local_bn = nn.BatchNorm2d(128)
    model.local_relu = nn.ReLU(inplace=True)
    model.fc = nn.Linear(num_ftrs, 2)

    use_gpu = torch.cuda.is_available()
    use_gpu = False
    if use_gpu:
        model = model.cuda()
    if weight_file_path:
        '''  Start Load weights from Align ReId Modified model '''
        checkpoint = torch.load(weight_file_path, map_location=lambda storage, loc: storage)
        if ver == 1:
            checkpoint_dict = checkpoint['state_dicts'][0]
        else:
            checkpoint_dict = checkpoint
        load_state_dict(model, checkpoint_dict)
        '''  End Load weights from Align ReId Modified model '''
    model.eval()

    return model

if __name__ == '__main__':
    #new weight file
    weight_file_path = './ckpt.pth'
    ver = 1  # 1 for new weight file, 0 for original
    source_dir = '../Dataset/Poster/Occluded/'
    save_dir = '../results/Poster/New_Occluded/'
    # old weight file
    #weight_file_path = './model_weight.pth'
    #ver = 0  # 1 for new weight file, 0 for original
    #source_dir = '../Dataset/Poster/Occluded/'
    #save_dir = '../results/Poster/Orig_Occluded/'
    # Market1501/Occluded
    layers = ['relu', 'layer1', 'layer2', 'layer3', 'layer4']
    img_list = []
    for img in glob.glob(source_dir+'*.jpg'):
        img_list.append(img)
    # Load the model
    model = load_model(weight_file_path,ver)
    #save_dir must already exist. New folder for each image will be created.
    for img in img_list:
        img_path = img
        file_name_to_export = img_path[img_path.rfind('\\') + 1:img_path.rfind('.')]
        # Open CV preporcessing
        image = cv2.imread(img_path)
        image_prep = preprocess_image(image,True)
        out = model(image_prep)
        target_class = out.data.numpy().argmax()
        #Generate GradCams from various layers of netwrok
        for layer in layers:
            # Grad cam
            grad_cam = GradCam(model, target_layer=layer)
            # Generate cam mask
            cam = grad_cam.generate_cam(image_prep, target_class)
            # Save mask
            save_class_activation_on_image(image, cam, file_name_to_export,layer,save_dir)
    print('Grad cam completed')

