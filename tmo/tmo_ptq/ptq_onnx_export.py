# by yhpark 2024-10-15
# TensorRT Model Optimization PTQ example
import modelopt.torch.quantization as mtq

import torch
import torch.onnx
import torch.nn as nn
import onnx
import os
import sys
import timm
import numpy as np 
import copy

from torch.utils.data import DataLoader, Subset
import torchvision.transforms as transforms
import torchvision.datasets as datasets

sys.path.insert(1, os.path.join(sys.path[0], "../.."))
from utils import *
set_random_seed()

current_file_path = os.path.abspath(__file__)
current_directory = os.path.dirname(current_file_path)
print(f"current file path: {current_file_path}")
print(f"current directory: {current_directory}")

# Print version information for debugging purposes
print(f"PyTorch version: {torch.__version__}")
print(f"ONNX version: {onnx.__version__}")

# Set device to GPU if available, otherwise fallback to CPU
device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
print(f"Using device: {device}")

def main():
    
    batchNorm_folding = True
    # Quantization need calibration data. Setup calibration data loader
    batch_size = 32  
    workers = 4
    transform = transforms.Compose([
        transforms.Resize(256),             
        transforms.CenterCrop(224),         
        transforms.ToTensor(),              
        transforms.Normalize(mean=[0.485, 0.456, 0.406], 
                            std=[0.229, 0.224, 0.225]),
    ])

    # load dataset 
    calib_size = 512 # typically 128-512 samples
    train_dataset = datasets.ImageFolder(root=f'{current_directory}/../datasets/imagenet100/train', transform=transform)
    # Randomly select the specified number of dataset
    indices = np.random.choice(len(train_dataset), calib_size, replace=False)
    subset_train_dataset = Subset(train_dataset, indices)
    train_loader = DataLoader(dataset=subset_train_dataset, batch_size=batch_size, shuffle=True, num_workers=workers, pin_memory=True, sampler=None)
    class_count = len(train_dataset.classes)
    
    model_name = "resnet18"
    if batchNorm_folding:
        export_model_path = os.path.join(current_directory, 'onnx', f'{model_name}_{device.type}_ptq_bf.onnx')
    else:
        export_model_path = os.path.join(current_directory, 'onnx', f'{model_name}_{device.type}_ptq.onnx')
    # Ensure the export directory exists
    os.makedirs(os.path.dirname(export_model_path), exist_ok=True)
    
    # Load the pre-trained model
    model = timm.create_model(model_name=model_name, num_classes=class_count, pretrained=True).to(device)
    model_path = f'{current_directory}/../base_model/checkpoint/best_model.pth'
    if os.path.exists(model_path) is False:
        print('There is no weight file, train base_model')
        exit()
        
    if torch.cuda.is_available():
        checkpoint = torch.load(model_path, map_location=device)
    else:
        checkpoint = torch.load(model_path)
    model.load_state_dict(checkpoint)
    model.eval()  # Set model to evaluation mode
    
    if batchNorm_folding :
        # batch folding(conv + bn -> fused conv)
        model = fuse_bn_recursively(model) 
    model.to(device)

    # Select quantization config
    config = mtq.INT8_DEFAULT_CFG

    # Define forward_loop. Please wrap the data loader in the forward_loop
    def forward_loop(model):
        for i, (batch, target) in enumerate(train_loader):
            model(batch.to(device))

    # Quantize the model and perform calibration (PTQ)
    model = mtq.quantize(model, config, forward_loop)
    
    # Print quantization summary after successfully quantizing the model with mtq.quantize
    # This will show the quantizers inserted in the model and their configurations
    mtq.print_quant_summary(model)
    
    
    # Get model input size from the model configuration
    input_size = model.pretrained_cfg["input_size"]
    dummy_input = torch.randn(input_size, requires_grad=False).unsqueeze(0).to(device)  # Create a dummy input

    # Export the model to ONNX format
    with torch.no_grad():  # Disable gradients for efficiency
        torch.onnx.export(
            model, 
            dummy_input, 
            export_model_path, 
            opset_version=19, 
            input_names=["input"], 
            output_names=["output"]
        )
        print(f"ONNX model exported to: {export_model_path}")

    # Verify the exported ONNX model
    onnx_model = onnx.load(export_model_path)
    onnx.checker.check_model(onnx_model)  # Perform a validity check
    print("ONNX model validation successful!")
    

# Run the main function
if __name__ == "__main__":
    main()