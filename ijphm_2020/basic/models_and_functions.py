# ______          _           _     _ _ _     _   _      
# | ___ \        | |         | |   (_) (_)   | | (_)     
# | |_/ / __ ___ | |__   __ _| |__  _| |_ ___| |_ _  ___ 
# |  __/ '__/ _ \| '_ \ / _` | '_ \| | | / __| __| |/ __|
# | |  | | | (_) | |_) | (_| | |_) | | | \__ \ |_| | (__ 
# \_|  |_|  \___/|_.__/ \__,_|_.__/|_|_|_|___/\__|_|\___|
# ___  ___          _                 _                  
# |  \/  |         | |               (_)                 
# | .  . | ___  ___| |__   __ _ _ __  _  ___ ___         
# | |\/| |/ _ \/ __| '_ \ / _` | '_ \| |/ __/ __|        
# | |  | |  __/ (__| | | | (_| | | | | | (__\__ \        
# \_|  |_/\___|\___|_| |_|\__,_|_| |_|_|\___|___/        
#  _           _                     _                   
# | |         | |                   | |                  
# | |     __ _| |__   ___  _ __ __ _| |_ ___  _ __ _   _ 
# | |    / _` | '_ \ / _ \| '__/ _` | __/ _ \| '__| | | |
# | |___| (_| | |_) | (_) | | | (_| | || (_) | |  | |_| |
# \_____/\__,_|_.__/ \___/|_|  \__,_|\__\___/|_|   \__, |
#                                                   __/ |
#                                                  |___/ 
#														  
# MIT License
# 
# Copyright (c) 2019 Probabilistic Mechanics Laboratory
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
# ==============================================================================

import numpy as np

from pinn.layers import CumulativeDamageCell
from pinn.layers import SNCurve
from pinn.layers import inputsSelection, TableInterpolation

from tensorflow import concat, expand_dims
from tensorflow.keras import Sequential
from tensorflow.keras.losses import mean_squared_error as mse
from tensorflow.keras.optimizers import RMSprop
from tensorflow.keras.models import Model
from tensorflow.keras.layers import Input, Multiply, Lambda, Concatenate, RNN

# =============================================================================
#     MODELS AND FUNCTIONS
# =============================================================================

def arrange_table(table):
    data = np.transpose(np.asarray(np.transpose(table))[1:])
    if data.shape[1] == 1:
        data = np.repeat(data,2,axis=1)
    data = np.expand_dims(data,0)
    data = np.expand_dims(data,-1)
    space = np.asarray([np.asarray(table.iloc[:,0]),np.asarray([float(i) for i in table.columns[1:]])])
    table_shape = data.shape
    bounds = np.asarray([[np.min(space[0]),np.min(space[1])],[np.max(space[0]),np.max(space[1])]])
    return {'data':data, 'bounds':bounds, 'table_shape':table_shape}
    

def masked_loss(y_true, y_pred):
    inspectionArray = np.asarray([6*24*30*1,6*24*30*2,6*24*30*3,6*24*30*4,6*24*30*5,6*24*30*6-1])
    y_true_masked = y_true
    y_pred_masked = concat([[[y_pred[0,inspectionArray[0],0],y_pred[0,inspectionArray[1],0],y_pred[0,inspectionArray[2],0],y_pred[0,inspectionArray[3],0],y_pred[0,inspectionArray[4],0],y_pred[0,inspectionArray[5],0]]]],1)
    for batch in range(1,y_pred.shape[0]):
        y_pred_masked = concat([y_pred_masked, concat([[[y_pred[batch,inspectionArray[0],0],y_pred[batch,inspectionArray[1],0],y_pred[batch,inspectionArray[2],0],y_pred[batch,inspectionArray[3],0],y_pred[batch,inspectionArray[4],0],y_pred[batch,inspectionArray[5],0]]]],1)],0)
        
    y_pred_masked = expand_dims(y_pred_masked,-1)
    return mse(y_true_masked,y_pred_masked)


def create_rnn_model(greaseMLP, d0RNN, batch_input_shape, lowBounds_delgrs, upBounds_delgrs,
                 myDtype, return_sequences = False, unroll = False):

    placeHolder = Input(shape=(batch_input_shape[2]+1,)) #Adding states
    
    MLPOutputs = greaseMLP(placeHolder)
    
    scaledMLPOutputs = Lambda(lambda x, lowBounds_delgrs=lowBounds_delgrs, upBounds_delgrs=upBounds_delgrs:
        x*(upBounds_delgrs-lowBounds_delgrs)+lowBounds_delgrs)(MLPOutputs)

        
    functionalModel = Model(inputs = [placeHolder], outputs = [scaledMLPOutputs])
    
    "-------------------------------------------------------------------------"
    CDMCellHybrid = CumulativeDamageCell(model = functionalModel,
                                       batch_input_shape = batch_input_shape,
                                       dtype = myDtype,
                                       initial_damage = d0RNN)
    
    CDMRNNhybrid = RNN(cell = CDMCellHybrid,
                       return_sequences = return_sequences,
                       return_state = False,
                       batch_input_shape = batch_input_shape,
                       unroll = unroll)
    
    model = Sequential()
    model.add(CDMRNNhybrid)
    model.compile(loss= masked_loss, optimizer=RMSprop(1e-3), metrics=[masked_loss])
    return model


def create_pinn_model(a, b, Pu,
                 grid_array_aSKF, bounds_aSKF, table_shape_aSKF,
                 grid_array_kappa, bounds_kappa, table_shape_kappa,
                 grid_array_etac, bounds_etac, table_shape_etac,
                 d0RNN, batch_input_shape, 
                 selectdKappa, selectCycle, selectLoad, selectBTemp, myDtype, return_sequences = False, unroll = False):
    
    batch_adjusted_shape = (batch_input_shape[2]+1,) #Adding states
    placeHolder = Input(shape=(batch_input_shape[2]+1,)) #Adding states
    
    filterdKappaLayer = inputsSelection(batch_adjusted_shape, selectdKappa)(placeHolder)
    filterCycleLayer = inputsSelection(batch_adjusted_shape, selectCycle)(placeHolder)
    filterLoadLayer = inputsSelection(batch_adjusted_shape, selectLoad)(placeHolder)
    filterBTempLayer = inputsSelection(batch_adjusted_shape, selectBTemp)(placeHolder)
    
    physicalSpaceInvLoadLayer = Lambda(lambda x: (1/(10**x)))(filterLoadLayer)
    
    xvalKappaLayer = Concatenate(axis = -1)([filterBTempLayer,filterdKappaLayer])
    
    kappaLayer = TableInterpolation(table_shape = table_shape_kappa, dtype = myDtype, trainable=False)
    kappaLayer.build(input_shape = xvalKappaLayer.shape)
    kappaLayer.set_weights([grid_array_kappa, bounds_kappa])
    kappaLayer = kappaLayer(xvalKappaLayer)
    
    xvalEtacLayer = Concatenate(axis = -1)([kappaLayer,filterdKappaLayer])
    
    etacLayer = TableInterpolation(table_shape = table_shape_etac, dtype = myDtype, trainable=False)
    etacLayer.build(input_shape = xvalEtacLayer.shape)
    etacLayer.set_weights([grid_array_etac, bounds_etac])
    etacLayer = etacLayer(xvalEtacLayer)


    xvalLayer1 = Lambda(lambda x: Pu*x)(etacLayer)
    xvalLayer2 = Multiply()([xvalLayer1, physicalSpaceInvLoadLayer])
    
    xvalLayer = Concatenate(axis = -1)([xvalLayer2,kappaLayer])
    
    aSKFLayer = TableInterpolation(table_shape = table_shape_aSKF, dtype = myDtype, trainable=False)
    aSKFLayer.build(input_shape = xvalLayer.shape)
    aSKFLayer.set_weights([grid_array_aSKF, bounds_aSKF])
    aSKFLayer = aSKFLayer(xvalLayer)
    
    inverseaSKFLayer = Lambda(lambda x: (1/x))(aSKFLayer)
    
        
    sn_input_shape = (batch_input_shape[0], batch_input_shape[2])
    
    SNLayer = SNCurve(input_shape = sn_input_shape, dtype = myDtype, trainable=False)
    SNLayer.build(input_shape = sn_input_shape)
    SNLayer.set_weights([np.asarray([a, b], dtype = SNLayer.dtype)])
    SNLayer = SNLayer(filterLoadLayer)
    
    multiplyLayer1 = Multiply()([SNLayer, filterCycleLayer])
    
    multiplyLayer2 = Multiply()([multiplyLayer1, inverseaSKFLayer])
    
    functionalModel = Model(inputs = [placeHolder], outputs = [multiplyLayer2])

    "-------------------------------------------------------------------------"
    CDMCellHybrid = CumulativeDamageCell(model = functionalModel,
                                       batch_input_shape = batch_input_shape,
                                       dtype = myDtype,
                                       initial_damage = d0RNN)
    
    CDMRNNhybrid = RNN(cell = CDMCellHybrid,
                       return_sequences = return_sequences,
                       return_state = False,
                       batch_input_shape = batch_input_shape,
                       unroll = unroll)
    
    model = Sequential()
    model.add(CDMRNNhybrid)
    model.compile(loss='mse', optimizer=RMSprop(5e-4), metrics=['mae'])
    return model
