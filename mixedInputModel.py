#  Copyright 2017 Martin Haesemeyer. All rights reserved.
#
# Licensed under the MIT license

"""
Builds a gradient prediction network that uses a mixed input layer, i.e. the input is a 2D tensor of both behavior and
temperature variables on which convolution filters operate jointly
"""

import tensorflow as tf
import core
import numpy as np


def create_hidden_layer(name_sfx, prev_out, n_units, index):
    """
    Creates a hidden layer taking in information from another layer
    :param name_sfx: The name suffix to use for this layer
    :param prev_out: The output of the previous layer
    :param n_units: The number of units in the layer
    :param index: The index of this particular layer for variable association
    :return: weights, biases and layer units of hidden layer
    """
    w = core.create_weight_var("W_h_" + name_sfx, [prev_out.shape[1].value, n_units], WDECAY)
    b = core.create_bias_var("B_h_" + name_sfx, [n_units])
    scale = n_units / tf.reduce_sum(det_remove[index])
    h = tf.nn.relu((tf.matmul(prev_out, w) + b) * det_remove[index] * scale, "h_" + name_sfx)
    return w, b, h


def create_dense_layers(prev_out):
    """
    Creates the dense hidden layers of our model
    :param prev_out: The output of the previous layer
    :return: weigths, biases, layer units of last dense and list of intermediate layers if any
    """
    if N_HIDDEN < 2:
        w, b, h = create_hidden_layer("0", prev_out, N_DENSE[0], 0)
        return w, b, h, []
    else:
        intermediate = []
        drop = prev_out
        for l in range(N_HIDDEN - 1):
            w, b, h = create_hidden_layer(str(l), drop, N_DENSE[l], l)
            drop = tf.nn.dropout(h, keep_prob, name="h_drop_" + str(l))
            intermediate.append((w, b, h, drop))
        # last layer
        w, b, h = create_hidden_layer(str(N_HIDDEN-1), drop, N_DENSE[N_HIDDEN-1], N_HIDDEN-1)
        return w, b, h, intermediate


def create_output(prev_out):
    """
    Creates the output layer for reporting predicted temperature of all four behaviors
    :param prev_out: The output of the previous layer
    :return: weights, biases, output
    """
    w = core.create_weight_var("W_o", [prev_out.shape[1].value, 4], WDECAY)
    b = core.create_bias_var("B_o", [4])
    out = tf.add(tf.matmul(prev_out, w), b, name="m_out")
    return w, b, out


def name_det_remove(index):
    """
    Returns the name of the deterministic removal tensor for the given hidden layer
    """
    return "remove_{0}".format(index)


def feed_det_remove(feed_dict, values=None):
    """
    Adds our deterministic removal variables to the feed dict
    :param feed_dict: The feedict to which variables should be added
    :param values: Either None (all 1) or for each layers det_remove the desired multipliers
    """
    if values is not None and len(values) != N_HIDDEN:
        raise ValueError("Values has to be a list with one array for each hidden layer")
    if values is None:
        for i in range(N_HIDDEN):
            feed_dict[det_remove[i]] = np.ones(N_DENSE[i], dtype=np.float32)
    else:
        for i in range(N_HIDDEN):
            feed_dict[det_remove[i]] = values[i]
    return feed_dict


def create_feed_dict(x_vals, y_vals=None, keep=1.0, removal=None):
    """
    Creates a feed dictionary for the network model
    :param x_vals: The network input
    :param y_vals: The true output
    :param keep: The probability of keeping a unit in drop-out
    :param removal: List of deterministic removal vectors - for each element 0=remove, 1=keep
    :return: The model feeding dictionary
    """
    f_dict = {x_in: x_vals, keep_prob: keep}
    if y_vals is not None:
        f_dict[y_] = y_vals
    return feed_det_remove(f_dict, removal)


# Hyper parameters of the model
N_CONV_LAYERS = 40  # the number of convolution filters
N_DENSE = [512, 512, 512]  # the number of units in each hidden layer
N_HIDDEN = len(N_DENSE)  # the number of hidden layers
WDECAY = 1e-4  # weight decay constant
KEEP_TRAIN = 0.5  # keep probability during training

# globals
assert core.FRAME_RATE % core.MODEL_RATE == 0
t_bin = core.FRAME_RATE // core.MODEL_RATE  # bin input down to 5Hz
binned_size = core.FRAME_RATE * core.HIST_SECONDS // t_bin

# dropout probability placeholder
keep_prob = tf.placeholder(tf.float32, name="keep_prob")

# placeholders for deterministic dropout
det_remove = [tf.placeholder(tf.float32, shape=[ndense], name=name_det_remove(i)) for i, ndense in enumerate(N_DENSE)]


# Network structure
# model input: BATCHSIZE x (Temp,Move,Turn) x HISTORYSIZE x 1 CHANNEL
x_in = tf.placeholder(tf.float32, [None, 3, core.FRAME_RATE*core.HIST_SECONDS, 1], "x_in")
# real outputs: BATCHSIZE x (dT(Stay), dT(Straight), dT(Left), dT(Right))
y_ = tf.placeholder(tf.float32, [None, 4], "y_")
# data binning layer
xin_pool = core.create_meanpool2d("xin_pool", x_in, 1, t_bin)
# convolution layer
W_conv1 = core.create_weight_var("W_conv1", [3, binned_size, 1, N_CONV_LAYERS])
B_conv1 = core.create_bias_var("B_conv1", [N_CONV_LAYERS])
conv1 = core.create_conv2d("conv1", xin_pool, W_conv1)
h_conv1 = tf.nn.relu(conv1 + B_conv1, "h_conv1")
h_conv1_flat = tf.reshape(h_conv1, [-1, N_CONV_LAYERS], "h_conv1_flat")
# dense layers
w_h_last, b_h_last, h_last, interim = create_dense_layers(h_conv1_flat)
# dropout on last layer
h_drop_last = tf.nn.dropout(h_last, keep_prob, name="h_drop_"+str(N_HIDDEN-1))
# create output layer
w_out, b_out, m_out = create_output(h_drop_last)

# get model loss and training step
total_loss, sq_loss = core.get_loss(y_, m_out)
t_step = core.create_train_step(total_loss)

if __name__ == "__main__":
    import matplotlib.pyplot as pl
    import seaborn as sns
    from scipy.ndimage import gaussian_filter1d
    print("Testing mixedInputModel", flush=True)
    print("For each 'behavior' subpart attempt to learn different sums on standard normal distribution", flush=True)
    t_losses = []
    d_fracs = []
    with tf.Session() as sess:
        sess.run(tf.global_variables_initializer())
        for i in range(10000):
            xb1 = np.random.randn(100, 1, core.FRAME_RATE * core.HIST_SECONDS, 1)
            xb2 = xb1 ** 2
            xb2 -= 1  # expected average of xb1**2
            xb3 = xb1 ** 3
            xbatch = np.concatenate((xb1, xb2, xb3), 1)
            ybatch = np.c_[np.sum(xb2, axis=(1, 2)), np.sum(xb2 / 4, axis=(1, 2)),
                           np.sum(xb1, axis=(1, 2)), np.sum(xb1 / 2, axis=(1, 2))]
            # create feedict appropriate for evaluations
            fdict = create_feed_dict(xbatch, ybatch, 1.0)
            cur_l = sq_loss.eval(feed_dict=fdict)
            pred = m_out.eval(feed_dict=fdict)
            cur_d = np.median(np.abs((ybatch - pred) / ybatch))
            t_losses.append(cur_l)
            d_fracs.append(cur_d)
            if i % 200 == 0:
                print('step %d, training loss %g, delta fraction %g' % (i, cur_l, cur_d))
            # create feedict appropriate for *training*
            fdict = create_feed_dict(xbatch, ybatch, KEEP_TRAIN)
            t_step.run(feed_dict=fdict)
        weights_conv1 = W_conv1.eval()
        bias_conv1 = B_conv1.eval()

    w_ext = np.max(np.abs(weights_conv1))
    fig, ax = pl.subplots(ncols=int(np.sqrt(N_CONV_LAYERS)), nrows=int(np.sqrt(N_CONV_LAYERS)), frameon=False,
                          figsize=(14, 2.8))
    ax = ax.ravel()
    for i, a in enumerate(ax):
        sns.heatmap(weights_conv1[:, :, 0, i], ax=a, vmin=-w_ext, vmax=w_ext, center=0, cbar=False)
        a.axis("off")

    pl.figure()
    pl.plot(t_losses, 'o')
    pl.xlabel("Batch")
    pl.ylabel("Training loss")
    sns.despine()

    pl.figure()
    pl.plot(d_fracs, 'o')
    pl.plot(gaussian_filter1d(d_fracs, 25))
    pl.xlabel("Batch")
    pl.ylabel("Error fraction")
    sns.despine()
