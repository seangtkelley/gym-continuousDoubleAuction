# -*- coding: utf-8 -*-
"""CDA_env_RLlib_pyAPI_2_learned_agent_mod_r_v.ipynb

Automatically generated by Colaboratory.

"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
os.environ['RAY_DEBUG_DISABLE_MEMORY_MONITOR'] = "True"

import argparse
import gym
import random
import numpy as np

import ray
from ray import tune
from ray.rllib.utils import try_import_tf
from ray.tune.registry import register_env
from ray.rllib.models.tf.tf_modelv2 import TFModelV2
from ray.rllib.models.tf.fcnet_v2 import FullyConnectedNetwork
from ray.rllib.models import Model, ModelCatalog
from ray.rllib.policy.policy import Policy
from ray.rllib.agents.ppo import ppo
from ray.rllib.agents.ppo.ppo import PPOTrainer
from ray.rllib.agents.ppo.ppo_tf_policy import PPOTFPolicy
from ray.tune.logger import pretty_print

import sys
if "../" not in sys.path:
    sys.path.append("../")

from gym_continuousDoubleAuction.envs.continuousDoubleAuction_env import continuousDoubleAuctionEnv

tf = try_import_tf()

class CustomModel_1(Model):
    """
    Sample custom model with LSTM. 
    """

    def _lstm(self, Inputs, cell_size):
        s = tf.expand_dims(Inputs, axis=1, name='time_major')  # [time_step, feature] => [time_step, batch, feature]
        lstm_cell = tf.nn.rnn_cell.LSTMCell(cell_size)
        self.init_state = lstm_cell.zero_state(batch_size=1, dtype=tf.float32)
        # time_major means [time_step, batch, feature] while batch major means [batch, time_step, feature]
        outputs, self.final_state = tf.nn.dynamic_rnn(cell=lstm_cell, inputs=s, initial_state=self.init_state, time_major=True)
        lstm_out = tf.reshape(outputs, [-1, cell_size], name='flatten_rnn_outputs')  # joined state representation
        return lstm_out
    
    def _build_layers_v2(self, input_dict, num_outputs, options):
        hidden = 512
        cell_size = 256
        #S = input_dict["obs"]
        S = tf.layers.flatten(input_dict["obs"])
        with tf.variable_scope(tf.VariableScope(tf.AUTO_REUSE, "shared"),
                               reuse=tf.AUTO_REUSE,
                               auxiliary_name_scope=False):
            last_layer = tf.layers.dense(S, hidden, activation=tf.nn.relu, name="fc1")
        last_layer = tf.layers.dense(last_layer, hidden, activation=tf.nn.relu, name="fc2")
        last_layer = tf.layers.dense(last_layer, hidden, activation=tf.nn.relu, name="fc3")

        last_layer = self._lstm(last_layer, cell_size)

        output = tf.layers.dense(last_layer, num_outputs, activation=tf.nn.softmax, name="mu")

        return output, last_layer

def make_RandomPolicy(_seed):

    class RandomPolicy(Policy):
        """
        A hand-coded policy that returns random actions in the env (doesn't learn).
        """        
        
        def __init__(self, observation_space, action_space, config):
            self.observation_space = observation_space
            self.action_space = action_space
            self.action_space.seed(_seed)

        def compute_actions(self,
                            obs_batch,
                            state_batches,
                            prev_action_batch=None,
                            prev_reward_batch=None,
                            info_batch=None,
                            episodes=None,
                            **kwargs):
            """Compute actions on a batch of observations."""
            return [self.action_space.sample() for _ in obs_batch], [], {}

        def learn_on_batch(self, samples):
            """No learning."""
            #return {}
            pass

        def get_weights(self):
            pass

        def set_weights(self, weights):
            pass

    return RandomPolicy

# global

# Storage for on_train_result callback, use for plotting.
agt_0_reward_list = []
agt_1_reward_list = []
agt_2_reward_list = []
agt_3_reward_list = []
agt_0_NAV_list = []
agt_1_NAV_list = []
agt_2_NAV_list = []
agt_3_NAV_list = []

# RLlib config
num_workers = 1
num_envs_per_worker = 2
sample_batch_size = 32
train_batch_size = 128
num_iters = 110

# Chkpt & restore
local_dir="/content/gdrive/My Drive/Colab Notebooks/gym-continuousDoubleAuction/gym_continuousDoubleAuction/chkpt/"
chkpt_freq = 10
chkpt = 470
restore_path = "{}checkpoint_{}/checkpoint-{}".format(local_dir, chkpt, chkpt)
is_restore = True

# CDA_env args
num_agents = 4
num_trained_agent = 2 
num_policies = num_agents # Each agent is using a separate policy
num_of_traders = num_agents
tape_display_length = 10 
tick_size = 1
init_cash = 1000000
max_step = 1000 # per episode, -1 in arg.
is_render = False

# get obs & act spaces from dummy CDA env
single_CDA_env = continuousDoubleAuctionEnv(num_of_traders, init_cash, tick_size, tape_display_length, max_step, is_render)
obs_space = single_CDA_env.observation_space
act_space = single_CDA_env.action_space

# register CDA env with RLlib 
register_env("continuousDoubleAuction-v0", lambda _: continuousDoubleAuctionEnv(num_of_traders, 
                                                                                init_cash, 
                                                                                tick_size, 
                                                                                tape_display_length,
                                                                                max_step-1, 
                                                                                is_render))

# register custom model (neural network)
ModelCatalog.register_custom_model("model_disc", CustomModel_1) 

# start ray
ray.init(ignore_reinit_error=True, log_to_driver=True, webui_host='127.0.0.1', num_cpus=2)

# Policies

def gen_policy(i):
    """
    Each policy can have a different configuration (including custom model)
    """
    config = {"model": {"custom_model": "model_disc"},
              "gamma": 0.99,}
    return (None, obs_space, act_space, config)

def policy_mapper(agent_id):
    for i in range(num_agents):
        if agent_id == i:
            return "policy_{}".format(i)


# Dictionary of policies
policies = {"policy_{}".format(i): gen_policy(i) for i in range(num_policies)}


def set_agents_policies(policies):
    """
    Set 1st policy as PPO & override all other policies as RandomPolicy with
    different seed.
    """
    
    # set all agents to use random policy
    for i in range(num_agents):
        policies["policy_{}".format(i)] = (make_RandomPolicy(i), obs_space, act_space, {})
    
    # set agent 0 & 1 to use None (PPOTFPolicy)
    offset = 2 # num of trained agents
    for i in range(num_agents-offset):
        #policies["policy_{}".format(i)] = (PPOTFPolicy, obs_space, act_space, {})
        policies["policy_{}".format(i)] = (None, obs_space, act_space, {})

    print('policies:', policies)
    return 0


set_agents_policies(policies)
policy_ids = list(policies.keys())

def on_episode_start(info):
    """
    info["episode"] is a MultiAgentEpisode object.
    """

    episode = info["episode"] 
    print("episode {} started".format(episode.episode_id))

    # hist_data dicts at 100 items max, will auto replace old with new item at 1st index.
    episode.hist_data["agt_0_NAV"] = []
    episode.hist_data["agt_1_NAV"] = []
    episode.hist_data["agt_2_NAV"] = []
    episode.hist_data["agt_3_NAV"] = []

def on_episode_end(info):
    """
    arg: {"env": .., "episode": ...}
    """

    episode = info["episode"]
    print("on_episode_end episode_id={}, length={}".format(episode.episode_id, episode.length))   
    
    last_info_0 = episode.last_info_for(0)
    last_info_1 = episode.last_info_for(1)
    last_info_2 = episode.last_info_for(2)
    last_info_3 = episode.last_info_for(3)

    episode.hist_data["agt_0_NAV"].append(last_info_0["NAV"])   
    episode.hist_data["agt_1_NAV"].append(last_info_1["NAV"])   
    episode.hist_data["agt_2_NAV"].append(last_info_2["NAV"])   
    episode.hist_data["agt_3_NAV"].append(last_info_3["NAV"])

def get_max_reward_ind(info):
    """
    Get index of the max reward of the trained policies in most recent episode.
    """

    recent_policies_rewards = []
    i = 0
    offset = 2 # 1st 2 items are non-related
    for k, v in info['result']['hist_stats'].items():
        if i >= offset and i < offset + num_trained_agent:
            recent_policies_rewards.append(v[0])
        i = i + 1 
    max_reward_ind = np.argmax(recent_policies_rewards)
    return max_reward_ind

def get_max_reward_policy_name(policies, max_reward_ind):
    """
    Get the policy name of the trained policy with the max reward in most recent episode.
    """

    train_policies_name = []
    i = 0
    for k,v in policies.items():
        if i < num_trained_agent:
            train_policies_name.append(k)
        i = i + 1
    max_reward_policy_name = train_policies_name[max_reward_ind]
    return train_policies_name, max_reward_policy_name

def _cp_weight(trainer, src, dest):
    """
    Copy weights of source policy to destination policy.
    """
    
    P0key_P1val = {}
    for (k,v), (k2,v2) in zip(trainer.get_policy(dest).get_weights().items(), 
                              trainer.get_policy(src).get_weights().items()):            
        P0key_P1val[k] = v2

    trainer.set_weights({dest:P0key_P1val, 
                         src:trainer.get_policy(src).get_weights()})

    for (k,v), (k2,v2) in zip(trainer.get_policy(dest).get_weights().items(), 
                              trainer.get_policy(src).get_weights().items()):            
        assert (v == v2).all()

def cp_weight(trainer, train_policies_name, max_reward_policy_name):
    """
    Copy weights of winning policy to weights of other trained policies.
    Winning is defined as getting max reward in the current episode.
    """

    for name in train_policies_name:
        if name != max_reward_policy_name:
            _cp_weight(trainer, max_reward_policy_name, name)

def all_eps_reward(info):
    agt_0_reward_list.append(info["result"]["hist_stats"]["policy_policy_0_reward"][0])
    agt_1_reward_list.append(info["result"]["hist_stats"]["policy_policy_1_reward"][0])
    agt_2_reward_list.append(info["result"]["hist_stats"]["policy_policy_2_reward"][0]) 
    agt_3_reward_list.append(info["result"]["hist_stats"]["policy_policy_3_reward"][0])

    print("agt_0_reward_list[0] = {}".format(agt_0_reward_list[0]))     
    print("agt_1_reward_list[1] = {}".format(agt_1_reward_list[0]))     
    print("agt_2_reward_list[2] = {}".format(agt_2_reward_list[0]))     
    print("agt_3_reward_list[3] = {}".format(agt_3_reward_list[0]))

def all_eps_NAV(info):
    agt_0_NAV_list.append(info["result"]["hist_stats"]["agt_0_NAV"][0]) 
    agt_1_NAV_list.append(info["result"]["hist_stats"]["agt_1_NAV"][0]) 
    agt_2_NAV_list.append(info["result"]["hist_stats"]["agt_2_NAV"][0]) 
    agt_3_NAV_list.append(info["result"]["hist_stats"]["agt_3_NAV"][0]) 
           
    print("agt_0_NAV_list[0] = {}".format(agt_0_NAV_list[0]))     
    print("agt_0_NAV_list[1] = {}".format(agt_1_NAV_list[0]))     
    print("agt_0_NAV_list[2] = {}".format(agt_2_NAV_list[0]))     
    print("agt_0_NAV_list[3] = {}".format(agt_3_NAV_list[0]))

def on_train_result(info):
    """
    info["trainer"] is the trainer object.

    info["result"] contains a bunch of info such as episodic rewards 
    for each policy in info["result"][hist_stats] dictionary.
    """

    # you can mutate the result dict to add new fields to return
    info["result"]["callback_ok"] = True
    
    trainer = info["trainer"] 
    max_reward_ind = get_max_reward_ind(info)
    train_policies_name, max_reward_policy_name = get_max_reward_policy_name(policies, max_reward_ind)
    cp_weight(trainer, train_policies_name, max_reward_policy_name)    
    all_eps_NAV(info)
    all_eps_reward(info)
    
    print("on_train_result ********** info['result'] {}".format(info["result"]))

# Training

def my_pyAPI_train():    
    config = ppo.DEFAULT_CONFIG.copy()
    config["multiagent"] = {"policies_to_train": ["policy_0", "policy_1"],
                            "policies": policies,
                            "policy_mapping_fn": policy_mapper,
                           }    
    config["num_workers"] = num_workers
    config["num_envs_per_worker"] = num_envs_per_worker  
    config["batch_mode"] = "complete_episodes"
    config["train_batch_size"] = train_batch_size # Training batch size, if applicable. Should be >= rollout_fragment_length.
                                     # Samples batches will be concatenated together to a batch of this size,
                                     # which is then passed to SGD.
    config["sample_batch_size"] = sample_batch_size # DEPRECATED_VALUE,
    config["log_level"] = "WARN"
    config["callbacks"] = {"on_episode_start": on_episode_start, 
                           "on_episode_step": None, 
                           "on_episode_end": on_episode_end, 
                           "on_sample_end": None,
                           "on_postprocess_traj": None,
                           "on_train_result": on_train_result,}

    trainer = ppo.PPOTrainer(config=config, env="continuousDoubleAuction-v0")
    if is_restore == True:
        trainer.restore(restore_path) 

    global hist_stats_len
    for i in range(num_iters):
        result = trainer.train()
        #print(result["custom_metrics"])
        #print(pretty_print(result))
        print("training loop = ", i + 1)        
        hist_stats_len = i + 1
        
        if i % chkpt_freq == 0:
            checkpoint = trainer.save(local_dir)
            print("checkpoint saved at", checkpoint)
    
    checkpoint = trainer.save(local_dir)
    print("checkpoint saved at", checkpoint)


# run everything
my_pyAPI_train()

"""
Plot episodic results
"""

import matplotlib.pyplot as plt

def plot_result(x,p0,p1,p2,p3,x_msg,y_msg):

    plt.figure(figsize=(10,10))

    plt.xlabel(x_msg)
    plt.ylabel(y_msg)

    plt.plot(x, p0, 'r', label='P0, PPO') # plotting x, y
    plt.plot(x, p1, 'g', label='P1, PPO') 
    plt.plot(x, p2, 'b', label='P2, random') 
    plt.plot(x, p3, 'orange', label='P3, random') 

    plt.legend()
    plt.show()

x = range(num_iters)
p0 = np.cumsum(agt_0_reward_list)
p1 = np.cumsum(agt_1_reward_list)
p2 = np.cumsum(agt_2_reward_list)
p3 = np.cumsum(agt_3_reward_list)
plot_result(x,p0,p1,p2,p3,'episode','cumulative episodic reward')

x = range(num_iters)

p0 = np.cumsum([value - init_cash for value in agt_0_NAV_list])
p1 = np.cumsum([value - init_cash for value in agt_1_NAV_list])
p2 = np.cumsum([value - init_cash for value in agt_2_NAV_list])
p3 = np.cumsum([value - init_cash for value in agt_3_NAV_list])
plot_result(x,p0,p1,p2,p3,'episode','cumulative episodic NAV - init_cash')
