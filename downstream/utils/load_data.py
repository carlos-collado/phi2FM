# Standard Library
import os
from glob import glob
import lmdb
import pickle

# External Libraries
import buteo as beo
import numpy as np

# PyTorch
import torch
from torch.utils.data import Dataset, DataLoader, Subset, random_split
from torch.utils.data.distributed import DistributedSampler

from utils import config_lc
from utils import Prithvi_100M_config

import random
from torchvision import transforms
import math

# statistics used to normalize images before passing to the model
MEANS_PRITHVI = np.array(Prithvi_100M_config.data_mean).reshape(1, 1, -1)
STDS_PRITHVI = np.array(Prithvi_100M_config.data_std).reshape(1, 1, -1)
MEANS_PRITHVI_PHI2 = np.array(Prithvi_100M_config.data_mean_phi2).reshape(1, 1, -1)
STDS_PRITHVI_PHI2 = np.array(Prithvi_100M_config.data_std_phi2).reshape(1, 1, -1)

LC_MAP = config_lc.lc_model_map
# order S2 bands: 0-B02, 1-B03, 2-B04, 3-B08, 4-B05, 5-B06, 6-B07, 7-B8A, 8-B11, 9-B12
MEANS_SATMAE = np.array([1184.3824625, 1120.77120066, 1136.26026392, 1762.59530783, 1263.73947144, 1645.40315151,
                        1846.87040806, 1972.62420416, 1732.16362238, 1247.91870117])

STDS_SATMAE = np.array([650.2842772, 965.23119807,  948.9819932, 1364.38688993, 1108.06650639, 1258.36394548,
                       1233.1492281, 3545.66, 1310.36996126, 1087.6020813])

MEANS_SATMAE_PHI2 = 10000 * np.array([0.1072132 , 0.10218794, 0.0983548 , 0.22145009, 0.12199436, 0.19247645, 0.22734961, 0. , 0. , 0. ])

STDS_SATMAE_PHI2 = 10000 *  np.array([0.04608911, 0.04950009, 0.07192364, 0.10286225, 0.07146991, 0.08716079, 0.1045232 , 0. , 0. , 0. ])

MIN_MAJORTOM = np.array([0., 0., 0., 0., 0., 0., 0., 0.])
MAX_MAJORTOM = np.array([1.41421356, 1.41421356, 1.41421356, 1.41421356, 1.41421356, 1.41421356, 1.41421356, 1.41421356])


PHISAT_MIN = np.array([0., 0., 0., 0., 0., 0., 0., 0.])
PHISAT_MAX = np.array([100., 100., 100., 100., 100., 100., 100., 100.])
PHISAT_MEAN = np.array([39.91732045, 37.5492021, 37.54950869, 39.21091477, 44.2665634, 39.50358262, 43.62563718, 45.28759192])
PHISAT_STD = np.array([17.06368142, 17.08672835, 20.21215486, 17.8629414, 20.11975944, 20.02886564, 19.79381833, 20.16760416])


PROCESS_PHISAT = True


def to_one_hot_lc(y, class_labels = np.array([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10])):
    y_classification = np.isin(class_labels, y).astype(np.float32)
    return y_classification


def to_one_hot_building(y):
    mean_value = np.mean(y > 0)
    if mean_value < 0 or mean_value > 1:
        raise ValueError('Invalid values in building mask')

    classes = [mean_value == 0, 0 < mean_value <= 0.3, 0.3 < mean_value <= 0.6, 0.6 < mean_value <= 0.9, mean_value > 0.9]    
    y_classification = np.array([float(x) for x in classes], dtype=np.float32)
    return y_classification



def sentinelNormalize(x):
    if PROCESS_PHISAT:
        if x.shape[2] == 8:
            x = np.delete(x, 3, axis=2)
            zeros_shape = (x.shape[0], x.shape[1], 3)
            zeros = np.zeros(zeros_shape, dtype=x.dtype)
            x = np.concatenate((x, zeros), axis=2)
            
        min_value = MEANS_SATMAE_PHI2 - 2 * STDS_SATMAE_PHI2
        max_value = MEANS_SATMAE_PHI2 + 2 * STDS_SATMAE_PHI2
    
    else:
        min_value = MEANS_SATMAE - 2 * STDS_SATMAE
        max_value = MEANS_SATMAE + 2 * STDS_SATMAE

    img = (x - min_value) / (max_value - min_value + 1e-8) * 255.0
    img = np.clip(img, 0, 255).astype(np.float32)
    return img

def preprocess_image_prithvi(image):
    if PROCESS_PHISAT:
        if image.shape[2] == 8:
            image = np.delete(image, 3, axis=2)
            zeros_shape = (image.shape[0], image.shape[1], 3)
            zeros = np.zeros(zeros_shape, dtype=image.dtype)
            image = np.concatenate((image, zeros), axis=2)
        
        normalized = image.copy()
        normalized = ((image - MEANS_PRITHVI_PHI2) / STDS_PRITHVI_PHI2)

    else:
        # normalize image
        normalized = image.copy()
        normalized = ((image - MEANS_PRITHVI) / STDS_PRITHVI)
    normalized = normalized.astype(np.float32, copy=False)

    # normalized = torch.from_numpy(normalized.reshape(1, normalized.shape[0], 1, *normalized.shape[-2:])).to(torch.float32)
    return normalized

def callback_preprocess(x, y):
    if PROCESS_PHISAT:
        if x.shape[2] == 8:
            x = np.delete(x, 3, axis=2)
            zeros_shape = (x.shape[0], x.shape[1], 3)
            zeros = np.zeros(zeros_shape, dtype=x.dtype)
            x = np.concatenate((x, zeros), axis=2)

    x_norm = np.empty_like(x, dtype=np.float32)
    np.divide(x, 10000.0, out=x_norm)

    y = y.astype(np.float32, copy=False)

    return x_norm, y


def callback_preprocess_satmae(x, y):
    if PROCESS_PHISAT:
        if x.shape[2] == 8:
            x = np.delete(x, 3, axis=2)
            zeros_shape = (x.shape[0], x.shape[1], 3)
            zeros = np.zeros(zeros_shape, dtype=x.dtype)
            x = np.concatenate((x, zeros), axis=2)
    x_norm = sentinelNormalize(x)
    y = y.astype(np.float32, copy=False)

    x_norm = x_norm[16:-16, 16:-16, :]
    if len(y.shape) > 2:
        y = y[16:-16, 16:-16, :]
    return x_norm, y


def callback_preprocess_prithvi(x, y):
    # order S2 bands: 0-B02, 1-B03, 2-B04, 3-B08, 4-B05, 5-B06, 6-B07, 7-B8A, 8-B11, 9-B12
    # HLS bands: 0-B02, 1-B03, 2-B04, 4-B05, 5-B06, 6-B07,
    if PROCESS_PHISAT:
        x = x[:, :, (0, 1, 2, 5, 6, 7)] 
    else:
        x = x[:, :, (0, 1, 2, 4, 5, 6)] 
    x_norm = preprocess_image_prithvi(x)
    y = y.astype(np.float32, copy=False)

    return x_norm, y


def callback_preprocess_landcover(x, y):
    if PROCESS_PHISAT:
        if x.shape[2] == 8:
            x = np.delete(x, 3, axis=2)
            zeros_shape = (x.shape[0], x.shape[1], 3)
            zeros = np.zeros(zeros_shape, dtype=x.dtype)
            x = np.concatenate((x, zeros), axis=2)

    x_norm = np.empty_like(x, dtype=np.float32)
    np.divide(x, 10000.0, out=x_norm)

    u, inv = np.unique(y, return_inverse=True)
    y = np.array([LC_MAP[val] for val in u])[inv].reshape(y.shape)
    
    return x_norm, y


def callback_preprocess_building_classification(x, y):
    if PROCESS_PHISAT:
        if x.shape[2] == 8:
            x = np.delete(x, 3, axis=2)
            zeros_shape = (x.shape[0], x.shape[1], 3)
            zeros = np.zeros(zeros_shape, dtype=x.dtype)
            x = np.concatenate((x, zeros), axis=2)

    x_norm = np.empty_like(x, dtype=np.float32)
    np.divide(x, 10000.0, out=x_norm)

    # y = to_one_hot_building(y)

    return x_norm, y



def callback_preprocess_landcover_satmae(x, y):
    x_norm = sentinelNormalize(x)

    u,inv = np.unique(y,return_inverse = True)
    y = np.array([LC_MAP[x] for x in u])[inv].reshape(y.shape)

    x_norm = x_norm[16:-16, 16:-16, :]
    y = y[16:-16, 16:-16, :]
    return x_norm, y


def callback_preprocess_landcover_prithvi(x, y):
    # order S2 bands: 0-B02, 1-B03, 2-B04, 3-B08, 4-B05, 5-B06, 6-B07, 7-B8A, 8-B11, 9-B12
    # HLS bands: 0-B02, 1-B03, 2-B04, 4-B05, 5-B06, 6-B07,
    if PROCESS_PHISAT:
        x = x[:, :, (0, 1, 2, 5, 6, 7)] # throw away unused bands
    else:
        x = x[:, :, (0, 1, 2, 4, 5, 6)] # throw away unused bands

    x_norm = preprocess_image_prithvi(x)
    u, inv = np.unique(y, return_inverse=True)
    y = np.array([LC_MAP[x] for x in u])[inv].reshape(y.shape)

    return x_norm, y


def callback_preprocess_phisatnet(x, y):
    assert x.shape[2] == 8, "Input x must have 8 channels for PHISAT2 classifier."
    
    x = np.sqrt(x)
    x = np.clip(x, PHISAT_MIN, PHISAT_MAX)
    x = (x - PHISAT_MEAN) / PHISAT_STD
    
    x = x.astype(np.float32, copy=False)
    
    u, inv = np.unique(y, return_inverse=True)
    y = np.array([LC_MAP[val] for val in u])[inv].reshape(y.shape)
    
    return x, y


def callback_postprocess_decoder(x, y):
    x = beo.channel_last_to_first(x)
    if len(y.shape) > 2:
        y = beo.channel_last_to_first(y)

    return torch.from_numpy(x), torch.from_numpy(y)


def callback_postprocess_decoder_geo(x, y):
    x = beo.channel_last_to_first(x)

    return torch.from_numpy(x), torch.from_numpy(y)


def callback_decoder(x, y):
    x, y = callback_preprocess(x, y)
    x, y = callback_postprocess_decoder(x, y)

    return x, y


def callback_decoder_landcover(x, y):
    x, y = callback_preprocess_landcover(x, y)
    x, y = callback_postprocess_decoder(x, y)

    return x, y


def callback_decoder_building_classification(x, y):
    x, y = callback_preprocess(x, y)
    x, y = callback_postprocess_decoder(x, y)

    return x, y


def callback_decoder_satmae(x, y):
    x, y = callback_preprocess_satmae(x, y)
    x, y = callback_postprocess_decoder(x, y)

    return x, y


def callback_decoder_landcover_satmae(x, y):
    x, y = callback_preprocess_landcover_satmae(x, y)
    x, y = callback_postprocess_decoder(x, y)

    return x, y

def callback_decoder_prithvi(x, y):
    x, y = callback_preprocess_prithvi(x, y)
    x, y = callback_postprocess_decoder(x, y)

    return x, y

def callback_decoder_landcover_prithvi(x, y):
    x, y = callback_preprocess_landcover_prithvi(x, y)
    x, y = callback_postprocess_decoder(x, y)

    return x, y


def callback_decoder_geo(x, y):
    x, y = callback_preprocess(x, y)
    x, y = callback_postprocess_decoder_geo(x, y)

    return x, y


def callback_decoder_phisatnet(x, y):
    x, y = callback_preprocess_phisatnet(x, y)
    x, y = callback_postprocess_decoder(x, y)

    return x, y


def load_data(x_train, y_train, x_val, y_val, x_test, y_test, x_inference, y_inference, device, with_augmentations=False, num_workers=0,
              batch_size=16, downstream_task=None, model_name=None, pad_to_10_bands=False):
    
    """
    Loads the data from the data folder.
    """
    global PROCESS_PHISAT
    PROCESS_PHISAT = pad_to_10_bands
    
    if model_name == 'SatMAE' or model_name == 'SatMAE_classifier':
        if downstream_task == 'lc':
            cb_decoder = callback_decoder_landcover_satmae
        else:
            cb_decoder = callback_decoder_satmae
    elif model_name == 'prithvi':
        if downstream_task == 'lc':
            cb_decoder = callback_decoder_landcover_prithvi
        else:
            cb_decoder = callback_decoder_prithvi
    elif model_name == 'phisatnet' or model_name == 'phisatnet_classifier':
        cb_decoder = callback_decoder_phisatnet
    else:
        if downstream_task=='lc':
            cb_decoder = callback_decoder_landcover
        elif downstream_task == 'building_classification':
            cb_decoder = callback_decoder_building_classification
        elif downstream_task == 'geo':
            cb_decoder = callback_decoder_geo
        else:
            cb_decoder = callback_decoder

    if with_augmentations:
        aug = [
                beo.AugmentationRotationXY(p=0.2, inplace=True),
                beo.AugmentationMirrorXY(p=0.2, inplace=True),
                # beo.AugmentationCutmix(p=0.2, inplace=True),
                beo.AugmentationNoiseNormal(p=0.2, inplace=True),
            ]

        if model_name == 'SatMAE':
            if downstream_task == 'lc':
                cb_preprocess = callback_preprocess_landcover_satmae
            else:
                cb_preprocess = callback_preprocess_satmae
        
        elif model_name == 'prithvi':
            if downstream_task == 'lc':
                cb_preprocess = callback_preprocess_landcover_prithvi
            else:
                cb_preprocess = callback_preprocess_prithvi
        elif model_name == 'phisatnet' or model_name == 'phisatnet_classifier':
            cb_preprocess = callback_preprocess_phisatnet
        else:
            if downstream_task=='lc':
                cb_preprocess = callback_preprocess_landcover
            else:
                cb_preprocess = callback_preprocess

        if downstream_task in ['geo', 'lc_classification', 'building_classification', 'roads_regression', 'coords']:
            cb_postprocess = callback_postprocess_decoder_geo
            aug = [
                beo.AugmentationRotation(p=0.2, inplace=True),
                beo.AugmentationMirror(p=0.2, inplace=True),
                # beo.AugmentationCutmix(p=0.2, inplace=True),
                beo.AugmentationNoiseNormal(p=0.2, inplace=True),
            ]
        else:
            cb_postprocess = callback_postprocess_decoder

        ds_train = beo.DatasetAugmentation(
            x_train, y_train,
            callback_pre_augmentation=cb_preprocess,
            callback_post_augmentation=cb_postprocess,
            augmentations=aug
        )
    else:
        ds_train = beo.Dataset(x_train, y_train, callback=cb_decoder)

    ds_test = beo.Dataset(x_test, y_test, callback=cb_decoder)
    ds_val = beo.Dataset(x_val, y_val, callback=cb_decoder)
    ds_inference = beo.Dataset(x_inference, y_inference, callback=cb_decoder)
    
    # import pdb; pdb.set_trace()

    dl_train = DataLoader(ds_train, batch_size=batch_size, shuffle=True, pin_memory=True, num_workers=num_workers,
                          drop_last=False, generator=torch.Generator(device))
    dl_test = DataLoader(ds_test, batch_size=batch_size, shuffle=False, pin_memory=True, num_workers=num_workers,
                         drop_last=False, generator=torch.Generator(device))
    dl_val = DataLoader(ds_val, batch_size=batch_size, shuffle=True, pin_memory=True, num_workers=num_workers,
                        drop_last=False, generator=torch.Generator(device))
    dl_inference = DataLoader(ds_inference, batch_size=batch_size, shuffle=False, pin_memory=True, num_workers=num_workers,
                              drop_last=False, generator=torch.Generator(device))

    return dl_train, dl_test, dl_val, dl_inference







