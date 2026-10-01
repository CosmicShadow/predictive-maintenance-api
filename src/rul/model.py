"""PyTorch LSTM that maps a window of sensor readings to remaining useful life."""

from __future__ import annotations

import torch
from torch import nn


class RULLSTM(nn.Module):
    def __init__(
        self, n_features: int, hidden_size: int = 64, num_layers: int = 2, dropout: float = 0.2
    ):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=n_features,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.head = nn.Sequential(
            nn.Linear(hidden_size, 32),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(32, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (batch, window, features). Use the hidden state after the last cycle.
        out, _ = self.lstm(x)
        return self.head(out[:, -1, :]).squeeze(-1)
