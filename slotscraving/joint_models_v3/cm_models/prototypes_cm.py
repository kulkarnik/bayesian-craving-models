## Latest version of the models for the slotscraving task
## Author: Kaustubh Kulkarni
## Original Date: June 2022
## Last Modified: Feb 5, 2023

import numpy as np
# import pandas as pd
import pymc as pm
import aesara.tensor as at
import aesara
from scipy import stats

from abc import ABC, abstractmethod

from sys import path
import os
from IPython.display import clear_output

import arviz as az

## Mixed EVRPE-CEC prototype class
class MixedPrototype(ABC):
    def __init__(self, longform, summary, project_dir, save_path, save_mood_path):
        self.name = None        
        self.longform = longform
        self.summary = summary
        self.pid_list = longform['PID'].unique()
        self.traces = {
            'money': {},
            'other': {}
        }
        self.mood_traces = {
            'money': {},
            'other': {}
        }
        self.project_dir = project_dir

        num_craving_trials = 20
        num_mood_trials = 12
        num_blocks = 2
        self.craving_inds = None
        self.mean_craving = np.zeros((num_blocks, len(self.pid_list)))
        self.std_craving = np.zeros((num_blocks, len(self.pid_list)))
        self.norm_cravings = np.zeros((num_blocks, len(self.pid_list), num_craving_trials))
        self.cravings = np.zeros((num_blocks, len(self.pid_list), num_craving_trials))

        self.mean_mood = np.zeros((num_blocks, len(self.pid_list)))
        self.std_mood = np.zeros((num_blocks, len(self.pid_list)))
        self.norm_moods = np.zeros((num_blocks, len(self.pid_list), num_mood_trials))
        self.moods = np.zeros((num_blocks, len(self.pid_list), num_mood_trials))

        self._calc_norm_cravings_mood()

        self.save_path = save_path
        self.save_mood_path = save_mood_path
    
    def _calc_norm_cravings_mood(self):
        for pid_num in range(len(self.pid_list)):
            for b, block in enumerate(['money', 'other']):
                pid = self.pid_list[pid_num]
                cravings = self.longform[(self.longform['PID']==pid)&(self.longform['Type']==block)]['Craving Rating'].values
                moods = self.longform[(self.longform['PID']==pid)&(self.longform['Type']==block)]['Mood Rating'].values
                craving_inds = np.squeeze(np.argwhere(cravings>-1))
                mood_inds = np.squeeze(np.argwhere(moods>-1))
                
                mask = np.ones(len(craving_inds), dtype=bool)
                mask[12] = False
                craving_inds = craving_inds[mask]
                cravings = cravings[craving_inds]
                moods = moods[mood_inds]
                
                self.craving_inds = craving_inds
                self.mean_craving[b, pid_num] = np.mean(cravings)
                self.std_craving[b, pid_num] = np.std(cravings)
                self.norm_cravings[b, pid_num, :] = stats.zscore(cravings)
                self.cravings[b, pid_num, :] = cravings

                self.mood_inds = mood_inds
                self.mean_mood[b, pid_num] = np.mean(moods)
                self.std_mood[b, pid_num] = np.std(moods)
                self.norm_moods[b, pid_num, :] = stats.zscore(moods)
                self.moods[b, pid_num, :] = moods

    @abstractmethod
    def update_Q(self, a, r, Qs, *args):
        pass

    def right_action_probs(self, actions, rewards, beta, cec_weight, *args):
        # Note that the first parameter is always the sample_beta, it is a required argument
        # Note that the second parameter is always the cec_weight, it is a required argument
        t_rewards = at.as_tensor_variable(rewards, dtype='int32')
        t_actions = at.as_tensor_variable(actions, dtype='int32')

        # Compute all loop vals
        # 0 - Q[left]
        # 1 - Q[right]
        # 2 - pred_craving
        # 3 - Q[t-1]
        # 4 - Q[t-2]
        # 5 - PE[t-1]
        # 6 - PE[t-2]
        loopvals =  at.zeros((7,), dtype='float64')
        loopvals, updates = aesara.scan(
            fn=self.update_Q,
            sequences=[t_actions, t_rewards],
            outputs_info=[loopvals],
            non_sequences=[*args])
        t_Qs = loopvals[:, :2]
        # t_pred_craving = pm.invlogit(loopvals[:, 2])
        t_pred_craving = pm.invlogit(loopvals[:, 2] + cec_weight*t_rewards)

        # Apply the sotfmax transformation
        t_Qs = t_Qs[:-1] * beta
        logp_actions = t_Qs - at.logsumexp(t_Qs, axis=1, keepdims=True)

        # Return the probabilities for the right action, in the original scale
        # Return predicted cravings
        return at.exp(logp_actions[:, 1]),  t_pred_craving
    
    def _just_Qs(self, actions, rewards, beta, cec_weight, *args):
        # Note that the first parameter is always the sample_beta, it is a required argument
        # Note that the second parameter is always the cec_weight, it is a required argument
        t_rewards = at.as_tensor_variable(rewards, dtype='int32')
        t_actions = at.as_tensor_variable(actions, dtype='int32')

        # Compute all loop vals
        # 0 - Q[left]
        # 1 - Q[right]
        # 2 - pred_craving
        # 3 - Q[t-1]
        # 4 - Q[t-2]
        # 5 - PE[t-1]
        # 6 - PE[t-2]
        loopvals =  at.zeros((7,), dtype='float64')
        loopvals, updates = aesara.scan(
            fn=self.update_Q,
            sequences=[t_actions, t_rewards],
            outputs_info=[loopvals],
            non_sequences=[*args])
        t_Qs = loopvals[:, :2]
        # t_pred_craving = pm.invlogit(loopvals[:, 2])
        t_pred_craving = pm.invlogit(loopvals[:, 2] + cec_weight*t_rewards)

        return loopvals, t_Qs, t_pred_craving
    
    def _load_act_rew_craving_mood(self, pid_num, block, norm=True):
        pid = self.pid_list[pid_num]
        b = 0 if block=='money' else 1
        act = self.longform[(self.longform['PID']==pid) & (self.longform['Type']==block)]['Action'].values
        rew = self.longform[(self.longform['PID']==pid) & (self.longform['Type']==block)]['Reward'].values
        if norm:
            crav = self.norm_cravings[b, pid_num, :]
            mood = self.norm_moods[b, pid_num, :]
        else:
            crav = self.cravings[b, pid_num, :]
            mood = self.moods[b, pid_num, :]
        return act, rew, crav, mood
    
    @abstractmethod
    def _define_priors(self):
        # Beta must be returned first!
        pass

    def fit(self, pid_num, block):
        pid = self.pid_list[pid_num]
        if self.save_path is not None:
            if not os.path.exists(f'{self.save_path}/{self.name}/'):
                os.makedirs(f'{self.save_path}/{self.name}/')
            filestr = f'{self.save_path}/{self.name}/{block}_{pid}.nc'
            if os.path.exists(filestr):
                print(f'PID: {pid}, Block {block} exists, loading from file...')
                self.traces[block][pid] = az.from_netcdf(filestr)
                return
        act, rew, cravings, moods = self._load_act_rew_craving_mood(pid_num, block, norm=False)
        with pm.Model() as model:
            priors = self._define_priors()
            action_probs, craving_pred = self.right_action_probs(act, rew, *priors)

            like = pm.Bernoulli('like', p=action_probs, observed=act[1:])
            probs_craving = pm.Deterministic('probs_craving', craving_pred[self.craving_inds-1])
            craving_like = pm.Binomial('craving_like', n=50, p=probs_craving, observed=cravings)
            
            self.traces[block][pid] = pm.sample(step=pm.Metropolis())
            # self.traces[block][pid].extend(pm.sample_prior_predictive())
            pm.sample_posterior_predictive(self.traces[block][pid], extend_inferencedata=True)
            if self.save_path is not None:
                self.traces[block][pid].to_netcdf(filestr)
    
    def fit_mood(self, pid_num, block):
        if self.save_mood_path is None:
            return 'No mood save path specified'
        pid = self.pid_list[pid_num]
        if self.save_mood_path is not None:
            if not os.path.exists(f'{self.save_mood_path}/{self.name}/'):
                os.makedirs(f'{self.save_mood_path}/{self.name}/')
            filestr = f'{self.save_mood_path}/{self.name}/{block}_{pid}.nc'
            if os.path.exists(filestr):
                print(f'PID: {pid}, Block {block} exists, loading from file...')
                self.mood_traces[block][pid] = az.from_netcdf(filestr)
                return
        act, rew, cravings, moods = self._load_act_rew_craving_mood(pid_num, block, norm=False)
        with pm.Model() as model:
            priors = self._define_priors()
            action_probs, mood_pred = self.right_action_probs(act, rew, *priors)

            like = pm.Bernoulli('like', p=action_probs, observed=act[1:])
            probs_mood = pm.Deterministic('probs_craving', mood_pred[self.mood_inds-1])
            mood_like = pm.Binomial('mood_like', n=50, p=probs_mood, observed=moods)
            
            self.mood_traces[block][pid] = pm.sample(step=pm.Metropolis())
            # self.mood_traces[block][pid].extend(pm.sample_prior_predictive())
            pm.sample_posterior_predictive(self.mood_traces[block][pid], extend_inferencedata=True)
            if self.save_mood_path is not None:
                self.mood_traces[block][pid].to_netcdf(filestr)
    
    def get_Q_vals(self, pid_num, block, parameter_names):
        pid = self.pid_list[pid_num]
        pid_trace = self.traces[block][pid]
        beta = float(pid_trace.posterior.beta.mean())
        cec_weight = float(pid_trace.posterior.cec_weight.mean())
        parameters = [float(pid_trace.posterior[p].mean()) for p in parameter_names]
        
        act, rew, cravings, moods = self._load_act_rew_craving_mood(pid_num, block, norm=False)
        # with pm.Model() as model:
        # Note that the first parameter is always the sample_beta, it is a required argument
        # Note that the second parameter is always the cec_weight, it is a required argument
        t_rewards = at.as_tensor_variable(rew, dtype='int32')
        t_actions = at.as_tensor_variable(act, dtype='int32')

        # Compute all loop vals
        # 0 - Q[left]
        # 1 - Q[right]
        # 2 - pred_craving
        # 3 - Q[t-1]
        # 4 - Q[t-2]
        # 5 - PE[t-1]
        # 6 - PE[t-2]
        loopvals =  at.zeros((7,), dtype='float64')
        loopvals, updates = aesara.scan(
            fn=self.update_Q,
            sequences=[t_actions, t_rewards],
            outputs_info=[loopvals],
            non_sequences=[*parameters])
        t_Qs = loopvals[:, :2]
        # t_pred_craving = pm.invlogit(loopvals[:, 2])
        t_pred_craving = pm.invlogit(loopvals[:, 2] + cec_weight*t_rewards)

        return loopvals.eval(), t_Qs.eval(), t_pred_craving.eval()

class MixedPrototype_noCEC(ABC):
    def __init__(self, longform, summary, project_dir, save_path, save_mood_path):
        self.name = None        
        self.longform = longform
        self.summary = summary
        self.pid_list = longform['PID'].unique()
        self.traces = {
            'money': {},
            'other': {}
        }
        self.mood_traces = {
            'money': {},
            'other': {}
        }
        self.project_dir = project_dir

        num_craving_trials = 20
        num_mood_trials = 12
        num_blocks = 2
        self.craving_inds = None
        self.mean_craving = np.zeros((num_blocks, len(self.pid_list)))
        self.std_craving = np.zeros((num_blocks, len(self.pid_list)))
        self.norm_cravings = np.zeros((num_blocks, len(self.pid_list), num_craving_trials))
        self.cravings = np.zeros((num_blocks, len(self.pid_list), num_craving_trials))

        self.mean_mood = np.zeros((num_blocks, len(self.pid_list)))
        self.std_mood = np.zeros((num_blocks, len(self.pid_list)))
        self.norm_moods = np.zeros((num_blocks, len(self.pid_list), num_mood_trials))
        self.moods = np.zeros((num_blocks, len(self.pid_list), num_mood_trials))

        self._calc_norm_cravings_mood()

        self.save_path = save_path
        self.save_mood_path = save_mood_path
    
    def _calc_norm_cravings_mood(self):
        for pid_num in range(len(self.pid_list)):
            for b, block in enumerate(['money', 'other']):
                pid = self.pid_list[pid_num]
                cravings = self.longform[(self.longform['PID']==pid)&(self.longform['Type']==block)]['Craving Rating'].values
                moods = self.longform[(self.longform['PID']==pid)&(self.longform['Type']==block)]['Mood Rating'].values
                craving_inds = np.squeeze(np.argwhere(cravings>-1))
                mood_inds = np.squeeze(np.argwhere(moods>-1))
                
                mask = np.ones(len(craving_inds), dtype=bool)
                mask[12] = False
                craving_inds = craving_inds[mask]
                cravings = cravings[craving_inds]
                moods = moods[mood_inds]
                
                self.craving_inds = craving_inds
                self.mean_craving[b, pid_num] = np.mean(cravings)
                self.std_craving[b, pid_num] = np.std(cravings)
                self.norm_cravings[b, pid_num, :] = stats.zscore(cravings)
                self.cravings[b, pid_num, :] = cravings

                self.mood_inds = mood_inds
                self.mean_mood[b, pid_num] = np.mean(moods)
                self.std_mood[b, pid_num] = np.std(moods)
                self.norm_moods[b, pid_num, :] = stats.zscore(moods)
                self.moods[b, pid_num, :] = moods

    @abstractmethod
    def update_Q(self, a, r, Qs, *args):
        pass

    def right_action_probs(self, actions, rewards, beta, *args):
        # Note that the first parameter is always the sample_beta, it is a required argument
        t_rewards = at.as_tensor_variable(rewards, dtype='int32')
        t_actions = at.as_tensor_variable(actions, dtype='int32')

        # Compute all loop vals
        # 0 - Q[left]
        # 1 - Q[right]
        # 2 - pred_craving
        # 3 - Q[t-1]
        # 4 - Q[t-2]
        # 5 - PE[t-1]
        # 6 - PE[t-2]
        loopvals =  at.zeros((7,), dtype='float64')
        loopvals, updates = aesara.scan(
            fn=self.update_Q,
            sequences=[t_actions, t_rewards],
            outputs_info=[loopvals],
            non_sequences=[*args])
        t_Qs = loopvals[:, :2]
        # t_pred_craving = pm.invlogit(loopvals[:, 2])
        t_pred_craving = pm.invlogit(loopvals[:, 2])

        # Apply the sotfmax transformation
        t_Qs = t_Qs[:-1] * beta
        logp_actions = t_Qs - at.logsumexp(t_Qs, axis=1, keepdims=True)

        # Return the probabilities for the right action, in the original scale
        # Return predicted cravings
        return at.exp(logp_actions[:, 1]),  t_pred_craving
    
    def _just_Qs(self, actions, rewards, beta, *args):
        # Note that the first parameter is always the sample_beta, it is a required argument
        t_rewards = at.as_tensor_variable(rewards, dtype='int32')
        t_actions = at.as_tensor_variable(actions, dtype='int32')

        # Compute all loop vals
        # 0 - Q[left]
        # 1 - Q[right]
        # 2 - pred_craving
        # 3 - Q[t-1]
        # 4 - Q[t-2]
        # 5 - PE[t-1]
        # 6 - PE[t-2]
        loopvals =  at.zeros((7,), dtype='float64')
        loopvals, updates = aesara.scan(
            fn=self.update_Q,
            sequences=[t_actions, t_rewards],
            outputs_info=[loopvals],
            non_sequences=[*args])
        t_Qs = loopvals[:, :2]
        # t_pred_craving = pm.invlogit(loopvals[:, 2])
        t_pred_craving = pm.invlogit(loopvals[:, 2])

        return loopvals, t_Qs, t_pred_craving
    
    def _load_act_rew_craving_mood(self, pid_num, block, norm=True):
        pid = self.pid_list[pid_num]
        b = 0 if block=='money' else 1
        act = self.longform[(self.longform['PID']==pid) & (self.longform['Type']==block)]['Action'].values
        rew = self.longform[(self.longform['PID']==pid) & (self.longform['Type']==block)]['Reward'].values
        if norm:
            crav = self.norm_cravings[b, pid_num, :]
            mood = self.norm_moods[b, pid_num, :]
        else:
            crav = self.cravings[b, pid_num, :]
            mood = self.moods[b, pid_num, :]
        return act, rew, crav, mood
    
    @abstractmethod
    def _define_priors(self):
        # Beta must be returned first!
        pass

    def fit(self, pid_num, block):
        pid = self.pid_list[pid_num]
        if self.save_path is not None:
            if not os.path.exists(f'{self.save_path}/{self.name}/'):
                os.makedirs(f'{self.save_path}/{self.name}/')
            filestr = f'{self.save_path}/{self.name}/{block}_{pid}.nc'
            if os.path.exists(filestr):
                print(f'PID: {pid}, Block {block} exists, loading from file...')
                self.traces[block][pid] = az.from_netcdf(filestr)
                return
        act, rew, cravings, moods = self._load_act_rew_craving_mood(pid_num, block, norm=False)
        with pm.Model() as model:
            priors = self._define_priors()
            action_probs, craving_pred = self.right_action_probs(act, rew, *priors)

            like = pm.Bernoulli('like', p=action_probs, observed=act[1:])
            probs_craving = pm.Deterministic('probs_craving', craving_pred[self.craving_inds-1])
            craving_like = pm.Binomial('craving_like', n=50, p=probs_craving, observed=cravings)
            
            self.traces[block][pid] = pm.sample(step=pm.Metropolis())
            # self.traces[block][pid].extend(pm.sample_prior_predictive())
            pm.sample_posterior_predictive(self.traces[block][pid], extend_inferencedata=True)
            if self.save_path is not None:
                self.traces[block][pid].to_netcdf(filestr)
    
    def fit_mood(self, pid_num, block):
        if self.save_mood_path is None:
            return 'No mood save path specified'
        pid = self.pid_list[pid_num]
        if self.save_mood_path is not None:
            if not os.path.exists(f'{self.save_mood_path}/{self.name}/'):
                os.makedirs(f'{self.save_mood_path}/{self.name}/')
            filestr = f'{self.save_mood_path}/{self.name}/{block}_{pid}.nc'
            if os.path.exists(filestr):
                print(f'PID: {pid}, Block {block} exists, loading from file...')
                self.mood_traces[block][pid] = az.from_netcdf(filestr)
                return
        act, rew, cravings, moods = self._load_act_rew_craving_mood(pid_num, block, norm=False)
        with pm.Model() as model:
            priors = self._define_priors()
            action_probs, mood_pred = self.right_action_probs(act, rew, *priors)

            like = pm.Bernoulli('like', p=action_probs, observed=act[1:])
            probs_mood = pm.Deterministic('probs_craving', mood_pred[self.mood_inds-1])
            mood_like = pm.Binomial('mood_like', n=50, p=probs_mood, observed=moods)
            
            self.mood_traces[block][pid] = pm.sample(step=pm.Metropolis())
            # self.mood_traces[block][pid].extend(pm.sample_prior_predictive())
            pm.sample_posterior_predictive(self.mood_traces[block][pid], extend_inferencedata=True)
            if self.save_mood_path is not None:
                self.mood_traces[block][pid].to_netcdf(filestr)
    
    def get_Q_vals(self, pid_num, block, parameter_names):
        pid = self.pid_list[pid_num]
        pid_trace = self.traces[block][pid]
        beta = float(pid_trace.posterior.beta.mean())
        parameters = [float(pid_trace.posterior[p].mean()) for p in parameter_names]
        
        act, rew, cravings, moods = self._load_act_rew_craving_mood(pid_num, block, norm=False)
        # with pm.Model() as model:
        # Note that the first parameter is always the sample_beta, it is a required argument
        t_rewards = at.as_tensor_variable(rew, dtype='int32')
        t_actions = at.as_tensor_variable(act, dtype='int32')

        # Compute all loop vals
        # 0 - Q[left]
        # 1 - Q[right]
        # 2 - pred_craving
        # 3 - Q[t-1]
        # 4 - Q[t-2]
        # 5 - PE[t-1]
        # 6 - PE[t-2]
        loopvals =  at.zeros((7,), dtype='float64')
        loopvals, updates = aesara.scan(
            fn=self.update_Q,
            sequences=[t_actions, t_rewards],
            outputs_info=[loopvals],
            non_sequences=[*parameters])
        t_Qs = loopvals[:, :2]
        # t_pred_craving = pm.invlogit(loopvals[:, 2])
        t_pred_craving = pm.invlogit(loopvals[:, 2])

        return loopvals.eval(), t_Qs.eval(), t_pred_craving.eval()


## Mixed EVRPE-CEC prototype class, designed for active beta models
class MixedPrototype_Beta(ABC):
    def __init__(self, longform, summary, project_dir, save_path, save_mood_path):
        self.name = None        
        self.longform = longform
        self.summary = summary
        self.pid_list = longform['PID'].unique()
        self.traces = {
            'money': {},
            'other': {}
        }
        self.mood_traces = {
            'money': {},
            'other': {}
        }
        self.project_dir = project_dir

        num_craving_trials = 20
        num_mood_trials = 12
        num_blocks = 2
        
        self.craving_inds = None
        self.mean_craving = np.zeros((num_blocks, len(self.pid_list)))
        self.std_craving = np.zeros((num_blocks, len(self.pid_list)))
        self.norm_cravings = np.zeros((num_blocks, len(self.pid_list), num_craving_trials))
        self.cravings = np.zeros((num_blocks, len(self.pid_list), num_craving_trials))

        self.mood_inds = None
        self.mean_mood = np.zeros((num_blocks, len(self.pid_list)))
        self.std_mood = np.zeros((num_blocks, len(self.pid_list)))
        self.norm_moods = np.zeros((num_blocks, len(self.pid_list), num_mood_trials))
        self.moods = np.zeros((num_blocks, len(self.pid_list), num_mood_trials))
        
        self._calc_norm_cravings_moods()

        self.save_path = save_path
        self.save_mood_path = save_mood_path
    
    def _calc_norm_cravings_moods(self):
        for pid_num in range(len(self.pid_list)):
            for b, block in enumerate(['money', 'other']):
                pid = self.pid_list[pid_num]
                cravings = self.longform[(self.longform['PID']==pid)&(self.longform['Type']==block)]['Craving Rating'].values
                moods = self.longform[(self.longform['PID']==pid)&(self.longform['Type']==block)]['Mood Rating'].values
                craving_inds = np.squeeze(np.argwhere(cravings>-1))
                mood_inds = np.squeeze(np.argwhere(moods>-1))
                
                mask = np.ones(len(craving_inds), dtype=bool)
                mask[12] = False
                craving_inds = craving_inds[mask]
                cravings = cravings[craving_inds]
                moods = moods[mood_inds]
                
                self.craving_inds = craving_inds
                self.mean_craving[b, pid_num] = np.mean(cravings)
                self.std_craving[b, pid_num] = np.std(cravings)
                self.norm_cravings[b, pid_num, :] = stats.zscore(cravings)
                self.cravings[b, pid_num, :] = cravings

                self.mood_inds = mood_inds
                self.mean_mood[b, pid_num] = np.mean(moods)
                self.std_mood[b, pid_num] = np.std(moods)
                self.norm_moods[b, pid_num, :] = stats.zscore(moods)
                self.moods[b, pid_num, :] = moods

    @abstractmethod
    def update_Q(self, a, r, Qs, *args):
        pass

    def right_action_probs(self, actions, rewards, beta, mod, cec_weight, *args):
        # Note that the first parameter is always the sample_beta, it is a required argument
        # Note that the second parameter is always the cec_weight, it is a required argument
        t_rewards = at.as_tensor_variable(rewards, dtype='int32')
        t_actions = at.as_tensor_variable(actions, dtype='int32')

        # Compute all loop vals
        # 0 - Q[left]
        # 1 - Q[right]
        # 2 - pred_craving
        # 3 - Q[t-1]
        # 4 - Q[t-2]
        # 5 - PE[t-1]
        # 6 - PE[t-2]
        loopvals =  at.zeros((7,), dtype='float64')
        loopvals, updates = aesara.scan(
            fn=self.update_Q,
            sequences=[t_actions, t_rewards],
            outputs_info=[loopvals],
            non_sequences=[*args])
        t_Qs = loopvals[:, :2]
        # t_pred_craving = pm.invlogit(loopvals[:, 2])
        t_pred_craving = pm.invlogit(loopvals[:, 2] + cec_weight*t_rewards)

        ## Compute the beta bias term
        biased_beta = mod*pm.math.invlogit(t_Qs[2]) + beta

        # Apply the sotfmax transformation
        t_Qs = t_Qs[:-1] * biased_beta[:-1]
        logp_actions = t_Qs - at.logsumexp(t_Qs, axis=1, keepdims=True)

        # Return the probabilities for the right action, in the original scale
        # Return predicted cravings
        return at.exp(logp_actions[:, 1]),  t_pred_craving
    
    def _load_act_rew_craving_mood(self, pid_num, block, norm=True):
        pid = self.pid_list[pid_num]
        b = 0 if block=='money' else 1
        act = self.longform[(self.longform['PID']==pid) & (self.longform['Type']==block)]['Action'].values
        rew = self.longform[(self.longform['PID']==pid) & (self.longform['Type']==block)]['Reward'].values
        if norm:
            crav = self.norm_cravings[b, pid_num, :]
            mood = self.norm_moods[b, pid_num, :]
        else:
            crav = self.cravings[b, pid_num, :]
            mood = self.moods[b, pid_num, :]
        return act, rew, crav, mood
    
    @abstractmethod
    def _define_priors(self):
        # Beta must be returned first!
        pass

    def fit(self, pid_num, block):
        pid = self.pid_list[pid_num]
        if self.save_path is not None:
            if not os.path.exists(f'{self.save_path}/{self.name}/'):
                os.makedirs(f'{self.save_path}/{self.name}/')
            filestr = f'{self.save_path}/{self.name}/{block}_{pid}.nc'
            if os.path.exists(filestr):
                print(f'PID: {pid}, Block {block} exists, loading from file...')
                self.traces[block][pid] = az.from_netcdf(filestr)
                return
        act, rew, cravings, moods = self._load_act_rew_craving_mood(pid_num, block, norm=False)
        with pm.Model() as model:
            priors = self._define_priors()
            action_probs, craving_pred = self.right_action_probs(act, rew, *priors)

            like = pm.Bernoulli('like', p=action_probs, observed=act[1:])
            probs_craving = pm.Deterministic('probs_craving', craving_pred[self.craving_inds-1])
            craving_like = pm.Binomial('craving_like', n=50, p=probs_craving, observed=cravings)
            
            self.traces[block][pid] = pm.sample(step=pm.Metropolis())
            # self.traces[block][pid].extend(pm.sample_prior_predictive())
            pm.sample_posterior_predictive(self.traces[block][pid], extend_inferencedata=True)
            if self.save_path is not None:
                self.traces[block][pid].to_netcdf(filestr)
    
    def fit_mood(self, pid_num, block):
        if self.save_mood_path is None:
            return 'No mood save path specified'
        pid = self.pid_list[pid_num]
        if self.save_mood_path is not None:
            if not os.path.exists(f'{self.save_mood_path}/{self.name}/'):
                os.makedirs(f'{self.save_mood_path}/{self.name}/')
            filestr = f'{self.save_mood_path}/{self.name}/{block}_{pid}.nc'
            if os.path.exists(filestr):
                print(f'PID: {pid}, Block {block} exists, loading from file...')
                self.mood_traces[block][pid] = az.from_netcdf(filestr)
                return
        act, rew, cravings, moods = self._load_act_rew_craving_mood(pid_num, block, norm=False)
        with pm.Model() as model:
            priors = self._define_priors()
            action_probs, mood_pred = self.right_action_probs(act, rew, *priors)

            like = pm.Bernoulli('like', p=action_probs, observed=act[1:])
            probs_mood = pm.Deterministic('probs_craving', mood_pred[self.mood_inds-1])
            mood_like = pm.Binomial('mood_like', n=50, p=probs_mood, observed=moods)
            
            self.mood_traces[block][pid] = pm.sample(step=pm.Metropolis())
            # self.mood_traces[block][pid].extend(pm.sample_prior_predictive())
            pm.sample_posterior_predictive(self.mood_traces[block][pid], extend_inferencedata=True)
            if self.save_mood_path is not None:
                self.mood_traces[block][pid].to_netcdf(filestr)
    
    def get_Q_vals(self, pid_num, block, parameter_names):
        pid = self.pid_list[pid_num]
        pid_trace = self.traces[block][pid]
        beta = float(pid_trace.posterior.beta.mean())
        cec_weight = float(pid_trace.posterior.cec_weight.mean())
        parameters = [float(pid_trace.posterior[p].mean()) for p in parameter_names]
        
        act, rew, cravings, moods = self._load_act_rew_craving_mood(pid_num, block, norm=False)
        # with pm.Model() as model:
        # Note that the first parameter is always the sample_beta, it is a required argument
        # Note that the second parameter is always the cec_weight, it is a required argument
        t_rewards = at.as_tensor_variable(rew, dtype='int32')
        t_actions = at.as_tensor_variable(act, dtype='int32')

        # Compute all loop vals
        # 0 - Q[left]
        # 1 - Q[right]
        # 2 - pred_craving
        # 3 - Q[t-1]
        # 4 - Q[t-2]
        # 5 - PE[t-1]
        # 6 - PE[t-2]
        loopvals =  at.zeros((7,), dtype='float64')
        loopvals, updates = aesara.scan(
            fn=self.update_Q,
            sequences=[t_actions, t_rewards],
            outputs_info=[loopvals],
            non_sequences=[*parameters])
        t_Qs = loopvals[:, :2]
        # t_pred_craving = pm.invlogit(loopvals[:, 2])
        t_pred_craving = pm.invlogit(loopvals[:, 2] + cec_weight*t_rewards)

        return loopvals.eval(), t_Qs.eval(), t_pred_craving.eval()

## Mixed EVRPE-CEC prototype class, designed for active beta models
class MixedPrototype_Beta_noCEC(ABC):
    def __init__(self, longform, summary, project_dir, save_path, save_mood_path):
        self.name = None        
        self.longform = longform
        self.summary = summary
        self.pid_list = longform['PID'].unique()
        self.traces = {
            'money': {},
            'other': {}
        }
        self.mood_traces = {
            'money': {},
            'other': {}
        }
        self.project_dir = project_dir

        num_craving_trials = 20
        num_mood_trials = 12
        num_blocks = 2
        
        self.craving_inds = None
        self.mean_craving = np.zeros((num_blocks, len(self.pid_list)))
        self.std_craving = np.zeros((num_blocks, len(self.pid_list)))
        self.norm_cravings = np.zeros((num_blocks, len(self.pid_list), num_craving_trials))
        self.cravings = np.zeros((num_blocks, len(self.pid_list), num_craving_trials))

        self.mood_inds = None
        self.mean_mood = np.zeros((num_blocks, len(self.pid_list)))
        self.std_mood = np.zeros((num_blocks, len(self.pid_list)))
        self.norm_moods = np.zeros((num_blocks, len(self.pid_list), num_mood_trials))
        self.moods = np.zeros((num_blocks, len(self.pid_list), num_mood_trials))
        
        self._calc_norm_cravings_moods()

        self.save_path = save_path
        self.save_mood_path = save_mood_path
    
    def _calc_norm_cravings_moods(self):
        for pid_num in range(len(self.pid_list)):
            for b, block in enumerate(['money', 'other']):
                pid = self.pid_list[pid_num]
                cravings = self.longform[(self.longform['PID']==pid)&(self.longform['Type']==block)]['Craving Rating'].values
                moods = self.longform[(self.longform['PID']==pid)&(self.longform['Type']==block)]['Mood Rating'].values
                craving_inds = np.squeeze(np.argwhere(cravings>-1))
                mood_inds = np.squeeze(np.argwhere(moods>-1))
                
                mask = np.ones(len(craving_inds), dtype=bool)
                mask[12] = False
                craving_inds = craving_inds[mask]
                cravings = cravings[craving_inds]
                moods = moods[mood_inds]
                
                self.craving_inds = craving_inds
                self.mean_craving[b, pid_num] = np.mean(cravings)
                self.std_craving[b, pid_num] = np.std(cravings)
                self.norm_cravings[b, pid_num, :] = stats.zscore(cravings)
                self.cravings[b, pid_num, :] = cravings

                self.mood_inds = mood_inds
                self.mean_mood[b, pid_num] = np.mean(moods)
                self.std_mood[b, pid_num] = np.std(moods)
                self.norm_moods[b, pid_num, :] = stats.zscore(moods)
                self.moods[b, pid_num, :] = moods

    @abstractmethod
    def update_Q(self, a, r, Qs, *args):
        pass

    def right_action_probs(self, actions, rewards, beta, mod, *args):
        # Note that the first parameter is always the sample_beta, it is a required argument
        t_rewards = at.as_tensor_variable(rewards, dtype='int32')
        t_actions = at.as_tensor_variable(actions, dtype='int32')

        # Compute all loop vals
        # 0 - Q[left]
        # 1 - Q[right]
        # 2 - pred_craving
        # 3 - Q[t-1]
        # 4 - Q[t-2]
        # 5 - PE[t-1]
        # 6 - PE[t-2]
        loopvals =  at.zeros((7,), dtype='float64')
        loopvals, updates = aesara.scan(
            fn=self.update_Q,
            sequences=[t_actions, t_rewards],
            outputs_info=[loopvals],
            non_sequences=[*args])
        t_Qs = loopvals[:, :2]
        # t_pred_craving = pm.invlogit(loopvals[:, 2])
        t_pred_craving = pm.invlogit(loopvals[:, 2])

        ## Compute the beta bias term
        biased_beta = mod*pm.math.invlogit(t_Qs[2]) + beta

        # Apply the sotfmax transformation
        t_Qs = t_Qs[:-1] * biased_beta[:-1]
        logp_actions = t_Qs - at.logsumexp(t_Qs, axis=1, keepdims=True)

        # Return the probabilities for the right action, in the original scale
        # Return predicted cravings
        return at.exp(logp_actions[:, 1]),  t_pred_craving
    
    def _load_act_rew_craving_mood(self, pid_num, block, norm=True):
        pid = self.pid_list[pid_num]
        b = 0 if block=='money' else 1
        act = self.longform[(self.longform['PID']==pid) & (self.longform['Type']==block)]['Action'].values
        rew = self.longform[(self.longform['PID']==pid) & (self.longform['Type']==block)]['Reward'].values
        if norm:
            crav = self.norm_cravings[b, pid_num, :]
            mood = self.norm_moods[b, pid_num, :]
        else:
            crav = self.cravings[b, pid_num, :]
            mood = self.moods[b, pid_num, :]
        return act, rew, crav, mood
    
    @abstractmethod
    def _define_priors(self):
        # Beta must be returned first!
        pass

    def fit(self, pid_num, block):
        pid = self.pid_list[pid_num]
        if self.save_path is not None:
            if not os.path.exists(f'{self.save_path}/{self.name}/'):
                os.makedirs(f'{self.save_path}/{self.name}/')
            filestr = f'{self.save_path}/{self.name}/{block}_{pid}.nc'
            if os.path.exists(filestr):
                print(f'PID: {pid}, Block {block} exists, loading from file...')
                self.traces[block][pid] = az.from_netcdf(filestr)
                return
        act, rew, cravings, moods = self._load_act_rew_craving_mood(pid_num, block, norm=False)
        with pm.Model() as model:
            priors = self._define_priors()
            action_probs, craving_pred = self.right_action_probs(act, rew, *priors)

            like = pm.Bernoulli('like', p=action_probs, observed=act[1:])
            probs_craving = pm.Deterministic('probs_craving', craving_pred[self.craving_inds-1])
            craving_like = pm.Binomial('craving_like', n=50, p=probs_craving, observed=cravings)
            
            self.traces[block][pid] = pm.sample(step=pm.Metropolis())
            # self.traces[block][pid].extend(pm.sample_prior_predictive())
            pm.sample_posterior_predictive(self.traces[block][pid], extend_inferencedata=True)
            if self.save_path is not None:
                self.traces[block][pid].to_netcdf(filestr)
    
    def fit_mood(self, pid_num, block):
        if self.save_mood_path is None:
            return 'No mood save path specified'
        pid = self.pid_list[pid_num]
        if self.save_mood_path is not None:
            if not os.path.exists(f'{self.save_mood_path}/{self.name}/'):
                os.makedirs(f'{self.save_mood_path}/{self.name}/')
            filestr = f'{self.save_mood_path}/{self.name}/{block}_{pid}.nc'
            if os.path.exists(filestr):
                print(f'PID: {pid}, Block {block} exists, loading from file...')
                self.mood_traces[block][pid] = az.from_netcdf(filestr)
                return
        act, rew, cravings, moods = self._load_act_rew_craving_mood(pid_num, block, norm=False)
        with pm.Model() as model:
            priors = self._define_priors()
            action_probs, mood_pred = self.right_action_probs(act, rew, *priors)

            like = pm.Bernoulli('like', p=action_probs, observed=act[1:])
            probs_mood = pm.Deterministic('probs_craving', mood_pred[self.mood_inds-1])
            mood_like = pm.Binomial('mood_like', n=50, p=probs_mood, observed=moods)
            
            self.mood_traces[block][pid] = pm.sample(step=pm.Metropolis())
            # self.mood_traces[block][pid].extend(pm.sample_prior_predictive())
            pm.sample_posterior_predictive(self.mood_traces[block][pid], extend_inferencedata=True)
            if self.save_mood_path is not None:
                self.mood_traces[block][pid].to_netcdf(filestr)
    
    def get_Q_vals(self, pid_num, block, parameter_names):
        pid = self.pid_list[pid_num]
        pid_trace = self.traces[block][pid]
        beta = float(pid_trace.posterior.beta.mean())
        parameters = [float(pid_trace.posterior[p].mean()) for p in parameter_names]
        
        act, rew, cravings, moods = self._load_act_rew_craving_mood(pid_num, block, norm=False)
        # with pm.Model() as model:
        # Note that the first parameter is always the sample_beta, it is a required argument
        t_rewards = at.as_tensor_variable(rew, dtype='int32')
        t_actions = at.as_tensor_variable(act, dtype='int32')

        # Compute all loop vals
        # 0 - Q[left]
        # 1 - Q[right]
        # 2 - pred_craving
        # 3 - Q[t-1]
        # 4 - Q[t-2]
        # 5 - PE[t-1]
        # 6 - PE[t-2]
        loopvals =  at.zeros((7,), dtype='float64')
        loopvals, updates = aesara.scan(
            fn=self.update_Q,
            sequences=[t_actions, t_rewards],
            outputs_info=[loopvals],
            non_sequences=[*parameters])
        t_Qs = loopvals[:, :2]
        # t_pred_craving = pm.invlogit(loopvals[:, 2])
        t_pred_craving = pm.invlogit(loopvals[:, 2])

        return loopvals.eval(), t_Qs.eval(), t_pred_craving.eval()
                
