#  Copyright 2018 Martin Haesemeyer. All rights reserved.
#
# Licensed under the MIT license

"""
Script to retrain all C elegans networks after ablating AFD and AWC/AIY like cells
"""

from core import GradientData, ModelData, CeGpNetworkModel
import analysis as a
import os
import h5py
import numpy as np
from global_defs import GlobalDefs
from mo_types import MoTypes


BATCHSIZE = 32  # the sample size of each training batch
TESTSIZE = 128  # the sample size of each test batch
EVAL_TEST_EVERY = 50
ce_like = [1, 7]
ce_unlike = [elem for elem in range(8) if elem not in ce_like]

# file definitions
base_path = "./model_data/CE_Adam_1e-4/"

paths_512 = [f+'/' for f in os.listdir(base_path) if "_3m512_" in f]


def mpath(path):
    return base_path + path[:-1]  # need to remove trailing slash


def retrain(m: CeGpNetworkModel, save_path: str, droplist, td_ix, filter_fun):
    def test():
        tbatch = testData.training_batch(TESTSIZE)
        pred = m.predict(tbatch[0], det_drop=droplist)
        re = a.rank_error(tbatch[1], pred)
        print("Global step: {0}. Rank test error {1}".format(global_step, re))
        test_errors.append(re)
        test_steps.append(global_step)
    # generate our non-t-branch training function
    train_func = m.get_filtered_train(filter_fun)
    test_errors = []
    test_steps = []
    chk_file = save_path + "/retrain.ckpt"
    epoch_1_size = train_list[td_ix[0]].data_size // BATCHSIZE
    epoch_2_size = train_list[td_ix[1]].data_size // BATCHSIZE
    global_step = 0
    # train one epoch each
    while global_step < epoch_1_size:
        if global_step % EVAL_TEST_EVERY == 0:
            test()
        data_batch = train_list[td_ix[0]].training_batch(BATCHSIZE)
        train_func(data_batch[0], data_batch[1], removal=droplist)
        global_step += 1
    while global_step < epoch_1_size + epoch_2_size:
        if global_step % EVAL_TEST_EVERY == 0:
            test()
        data_batch = train_list[td_ix[1]].training_batch(BATCHSIZE)
        train_func(data_batch[0], data_batch[1], removal=droplist)
        global_step += 1
    sf = m.save_state(chk_file, global_step, True)
    print("Retrained model saved in file {0}.".format(sf))
    error_file = h5py.File(save_path+"/losses.hdf5", "x")
    error_file.create_dataset("test_rank_errors", data=np.array(test_errors))
    error_file.create_dataset("test_eval", data=np.array(test_steps))
    error_file.close()


if __name__ == '__main__':
    # load training and test data
    tD_1 = GradientData.load("ce_gd_training_data.hdf5")
    tD_2 = GradientData.load("ce_gd_training_data_rev.hdf5")
    tD_2.copy_normalization(tD_1)
    train_list = [tD_1, tD_2]
    testData = GradientData.load("ce_gd_test_data_radial.hdf5")
    # enforce same scaling on testData as on trainingData
    testData.copy_normalization(tD_1)

    ana = a.Analyzer(MoTypes(True), tD_1.standards, None, "ce_activity_store.hdf5")

    # load cell unit ids and cluster ids
    dfile = h5py.File("stimFile.hdf5", 'r')
    tsin = np.array(dfile['sine_L_H_temp'])
    x = np.arange(tsin.size)  # stored at 20 Hz !
    xinterp = np.linspace(0, tsin.size, tsin.size * GlobalDefs.frame_rate // 20)
    temperature = np.interp(xinterp, x, tsin)
    dfile.close()
    all_ids = []
    for i, p in enumerate(paths_512):
        cell_res, ids = ana.temperature_activity(mpath(p), temperature, i)
        all_ids.append(ids)
    all_ids = np.hstack(all_ids)
    clfile = h5py.File("ce_cluster_info.hdf5", "r")
    clust_ids = np.array(clfile["clust_ids"])
    clfile.close()
    train_ix = [0, 1]
    for i, p in enumerate(paths_512):
        np.random.shuffle(train_ix)
        model_path = mpath(p)
        mdata = ModelData(model_path)
        # t-branch retrain
        cel_folder = model_path+"/cel_tbranch_retrain"
        model = None
        dlist = a.create_det_drop_list(i, clust_ids, all_ids, ce_like)
        if os.path.exists(cel_folder):
            print("Temperature branch retrain folder on model {0} already exists. Skipping.".format(p))
        else:
            os.mkdir(cel_folder)
            model = CeGpNetworkModel()
            model.load(mdata.ModelDefinition, mdata.LastCheckpoint)
            retrain(model, cel_folder, dlist, train_ix, lambda n: "_t_" in n)
        # m branch retrain
        cel_folder = model_path+"/cel_nontbranch_retrain"
        if os.path.exists(cel_folder):
            print("Shared branch retrain folder on model {0} already exists. Skipping.".format(p))
            continue
        os.mkdir(cel_folder)
        if model is not None:
            model.clear()
        model = CeGpNetworkModel()
        model.load(mdata.ModelDefinition, mdata.LastCheckpoint)
        retrain(model, cel_folder, dlist, train_ix, lambda n: "_m_" in n)
        model.clear()
