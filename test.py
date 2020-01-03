import cv2
import random
import numpy as np
import torch
import torch.nn as nn
from collections import deque
import os
from game.Game import Game
from nets import MyNet
from torch.distributions import Categorical
from tensorboardX import SummaryWriter
from config import args
from utils import *

'''AC模型实现（暂未实现）'''


class Trainer:
    def __init__(self, net_path):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.game = Game(level=2, train=True)
        self.image_size = args.image_size
        self.epochs = args.epochs
        self.MAX_EP_STEP = args.MAX_EP_STEP
        self.start_epsilon = args.start_epsilon
        self.end_epsilon = args.end_epsilon
        self.memory_size = args.memory_size
        self.gamma = args.gamma
        self.observe = args.observe
        self.net_path = net_path
        self.writer = SummaryWriter()
        # 定义一个最小正数eps用于分母相加，防止精度丢失的问题
        self.eps = np.finfo(np.float32).eps.item()
        self.net = MyNet().to(self.device)
        if os.path.exists(net_path):
            self.net.load_state_dict(torch.load(net_path))
        else:
            self.net.apply(self.weight_init)
        self.loss = nn.MSELoss()
        self.optimizer = torch.optim.Adam(self.net.parameters())

    def edit_image(self, image, width, height):
        image = cv2.cvtColor(cv2.resize(image, (width, height)), cv2.COLOR_BGR2GRAY)
        _, image = cv2.threshold(image, 1, 255, cv2.THRESH_BINARY)
        return image[None, :, :].astype(np.float32)

    def weight_init(self, net):
        if isinstance(net, nn.Conv2d) or isinstance(net, nn.Linear):
            nn.init.normal_(net.weight, mean=0., std=0.1)
            nn.init.constant_(net.bias, 0)

    # def select_action(self, state):
    #     action_prob, action_value = self.net(state)
    #     action_distribution = Categorical(action_prob)
    #     action = action_distribution.sample()
    #     # self.net.actions.append([action_distribution.log_prob(action), action_value])
    #     # return action.item()
    #     # print(action.item(), action_distribution.log_prob(action).item(), action_value.item())
    #     return action.item(), action_distribution.log_prob(action).item(), action_value.item()
    #
    # def get_v_value(self):
    #     R = 0
    #     v_values = []
    #
    #     for reward in self.net.rewards[::-1]:
    #         R = reward + self.gamma * R
    #         v_values.insert(0, R)
    #
    #     v_values = torch.Tensor(v_values)
    #     # 根据期望和方差做标准归一化
    #     v_value = (v_values - v_values.mean()) / (v_values.std() + self.eps)
    #     return v_value

    def train(self):
        image, reward, terminal = self.game.step(0)
        # 截取图片中有用的地方，下面重复的地方删掉，因为self.game.base_y=409.2所以要取整；self.game.screen_width=288
        image = self.edit_image(image[:self.game.screen_width, :int(self.game.base_y)], self.image_size,
                                self.image_size)
        image = torch.from_numpy(image).to(self.device)
        state = torch.cat([image for _ in range(4)]).unsqueeze(0)
        for i in range(self.epochs):
            buffer_state, buffer_action, buffer_reward = [], [], []
            for t in range(self.MAX_EP_STEP):
                # 更新探索值
                epsilon = self.end_epsilon + ((self.epochs - i) * (self.start_epsilon - self.end_epsilon) / self.epochs)

                if random.random() <= epsilon:
                    # 探索,0不动，1往上飞
                    action = np.random.choice([0, 1], 1, p=[0.9, 0.1])[0]
                    action = np.array([action], dtype=np.int64)
                    # action = np.array([random.randint(0, 1)], dtype=np.int64)
                    # print("-------- random action -------- ", action[0])
                else:
                    # 开发
                    action = self.net.select_action(state)
                next_state, reward, terminal = self.game.step(action)
                next_state = self.edit_image(next_state[:self.game.screen_width, :int(self.game.base_y)],
                                             self.image_size,
                                             self.image_size)
                next_state = torch.from_numpy(next_state).to(self.device)
                next_state = torch.cat([state[0, 1:, :, :], next_state]).unsqueeze(0)
                if t == args.MAX_EP_STEP - 1:
                    terminal = True
                buffer_state.append(state[0])
                action = torch.tensor(action)
                buffer_action.append(action)
                buffer_reward.append(reward)
                if terminal:
                    loss = push_and_pull(None, self.net, None, terminal, next_state, buffer_state,
                                         buffer_action, buffer_reward, self.gamma, self.device, False)
                    buffer_state, buffer_action, buffer_reward = [], [], []
                    self.optimizer.zero_grad()
                    loss.backward()
                    self.optimizer.step()
                    print(f"epoch:{i},epsilon:{epsilon},loss:{loss}")
                    self.writer.add_scalar("loss/loss", loss, i)
                    self.net.add_histogram(self.writer, i)
                    torch.save(self.net.state_dict(), self.net_path)
                state = next_state

            if i % 10 == 0:
                print(f"epoch:{i},loss:{loss}")
                self.writer.add_scalar("loss/loss", loss, i)
                self.net.add_histogram(self.writer, i)
                torch.save(self.net.state_dict(), self.net_path)

            # for _ in range(3000):
            #     action = self.select_action(state)
            #     # print(action)
            #     next_state, reward, terminal = self.game.step(action)
            #     self.net.rewards.append(reward)
            #     next_state = self.edit_image(next_state[:self.game.screen_width, :int(self.game.base_y)],
            #                                  self.image_size, self.image_size)
            #     next_state = torch.from_numpy(next_state).to(self.device)
            #     next_state = torch.cat([state[0, 1:, :, :], next_state]).unsqueeze(0)
            #     state = next_state
            #     # if terminal:
            #     #     print(reward)
            #     #     break
            # v_value = self.get_v_value()
            # actor_loss = []
            # critic_loss = []
            # for (log_prob, value), R in zip(self.net.actions, v_value.to(self.device)):
            #     # 求得动作优势
            #     advantage = value.item() - R
            #     actor_loss.append(log_prob * advantage)
            #     critic_loss.append(self.loss(value, R))
            # loss = torch.stack(actor_loss).sum() + torch.stack(critic_loss).sum()
            # self.optimizer.zero_grad()
            # loss.backward()
            # self.optimizer.step()
            #
            # self.net.actions = []
            # self.net.rewards = []
            #
            # print(f"epoch:{i},loss:{loss}")
            # self.writer.add_scalar("loss/loss", loss, i)
            # self.net.add_histogram(self.writer, i)
            # torch.save(self.net.state_dict(), self.net_path)


if __name__ == '__main__':
    trainer = Trainer("models/net_ac.pt")
    trainer.train()