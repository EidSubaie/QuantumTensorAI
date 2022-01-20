import torch
from torch import nn

from qtensor_ai.ParallelQTensor import ParallelTorchQkernelComposer, ParallelSimulator
from qtensor.optimisation.TensorNet import QtreeTensorNet
from qtensor.optimisation.Optimizer import DefaultOptimizer

        
'''This is a drop-in replacement of linear layers.'''
class QNN(nn.Module):
    
    def __init__(self, in_features, out_features, variational_layers=1, higher_order=False, optimizer=DefaultOptimizer()):
        super().__init__()
                
        '''Initializing module parameters'''
        self.higher_order = higher_order    
        
        '''Initializing simulator'''
        self.sim = ParallelSimulator
        
        '''Tree optimization'''
        init_inputs = torch.zeros(1, in_features, requires_grad=False);
        init_params = torch.zeros(1, in_features, variational_layers, requires_grad=False)
        com = ParallelTorchQkernelComposer(in_features)
        com.higher_order = higher_order
        com.energy_expectation(init_inputs, init_params)
        tn = QtreeTensorNet.from_qtree_gates(com.circuit)
        
        '''peo is the tensor network contraction order'''
        self.peo, tn = optimizer.optimize(tn)
        
        '''self.weight are model weights'''
        self.weight = nn.Parameter(torch.randn(out_features, in_features, variational_layers, dtype=torch.float32))
           
    def forward(self, x):
        n_batch, in_features = x.shape # (n_batch, in_features)
        out_features, in_features, variational_layers = self.weight.shape
        x = x.repeat(out_features, 1) # (out_features*n_batch, in_features)
        params = self.weight.unsqueeze(1) # (out_features, 1, in_features, variational_layers)
        params = params.expand(-1, n_batch, -1, -1) # (out_features, n_batch, in_features, variational_layers)
        params = params.reshape(out_features*n_batch, in_features, variational_layers) # (out_features*n_batch, in_features, variational_layers)
        com = ParallelTorchQkernelComposer(in_features)
        com.higher_order = self.higher_order
        com.energy_expectation(x, params)
        out = torch.real(self.sim.simulate_batch(com.circuit, peo=self.peo)) # (out_features*n_batch)
        out = out.reshape(out_features, n_batch) # (out_features, n_batch)
        out = out.permute(0, 1) # (n_batch, out_features)
        return out
    
        
'''This is an example for 1D convolution. The filter is replaced with the QNN.'''
class QConv1D(nn.Module):
    
    def __init__(self, in_channels, out_channels, kernel_size, variational_layers=1, higher_order=False, optimizer=DefaultOptimizer(), dilation=1, padding=0, stride=1):
        super().__init__()
                
        '''Initializing module parameters'''
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.kernel_size = kernel_size
        self.n_qubits = in_channels * kernel_size
        
        '''Defining unfold operation for convolution'''
        self.unfold = nn.Unfold(kernel_size=(kernel_size,1), dilation=(dilation,1), padding=(padding,0), stride=(stride,1))
        
        '''Defining multichannel filter to be convolved'''
        self.filter = QNN(self.n_qubits, out_channels, variational_layers, higher_order, optimizer)
        
    
    '''This function transforms batched, multichannel sequences into parallel values of convolution kernel inputs'''
    def memory_strided_im2col(self, x):
        # x has dimension (n_batch, in_channels, length)
        x = x.unsqueeze(-1)
        out = self.unfold(x)
        out = torch.transpose(out, 1, 2)
        # out has dimension (n_batch, L, kernel_size*in_channels=n_qubits)
        return out
    
    def forward(self, x):
        n_batch = x.size(0) # (n_batch, in_channels, length)
        x = self.memory_strided_im2col(x) # (n_batch, L, kernel_size*in_channels=n_qubits)
        x = x.reshape(-1, self.kernel_size*self.in_channels) # (n_batch*L, kernel_size*in_channels=n_qubits)
        output = self.filter(x) # (n_batch*L, out_channels)  
        output = output.reshape(n_batch, -1, self.out_channels) # (n_batch, L, out_channels) 
        output = output.permute(0,2,1) # (n_batch, out_channels, L)
        return output