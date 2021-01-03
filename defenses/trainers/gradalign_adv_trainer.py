import torch
import torch.nn as nn

from .adv_trainer import AdvTrainer

r"""
'Understanding and Improving Fast Adversarial Training'
[https://arxiv.org/abs/2007.02617]
[https://github.com/tml-epfl/understanding-fast-adv-training]

Attributes:
    self.model : model.
    self.device : device where model is.
    self.optimizer : optimizer.
    self.scheduler : scheduler (Automatically updated).
    self.max_epoch : total number of epochs.
    self.max_iter : total number of iterations.
    self.epoch : current epoch starts from 1 (Automatically updated).
    self.iter : current iters starts from 1 (Automatically updated).
        * e.g., is_last_batch = (self.iter == self.max_iter)
    self.record_keys : names of items returned by do_iter.
    
Arguments:
    model (nn.Module): model to train.
    eps (float): strength of the attack or maximum perturbation.
    alpha (float): alpha in the paper.
    grad_align_cos_lambda (float): parameter for the regularization term.

"""

class GradAlignAdvTrainer(AdvTrainer):
    def __init__(self, model, eps, alpha, grad_align_cos_lambda):
        super(GradAlignAdvTrainer, self).__init__("GradAlignAdvTrainer", model)
        self.record_keys = ["Loss", "CELoss", "GALoss"] # Must be same as the items returned by self._do_iter
        self.eps = eps
        self.alpha = alpha
        self.grad_align_cos_lambda = grad_align_cos_lambda
    
    def _l2_norm_batch(self, v):
        norms = (v ** 2).sum([1, 2, 3]) ** 0.5
        # norms[norms == 0] = np.inf
        return norms
    
    def _do_iter(self, train_data):
        r"""
        Overridden.
        """
        images, labels = train_data
        X = images.to(self.device)
        Y = labels.to(self.device)

        X_new = torch.cat([X.clone(), X.clone()], dim=0)
        Y_new = torch.cat([Y.clone(), Y.clone()], dim=0)

        delta1 = torch.empty_like(X).uniform_(-self.eps, self.eps)
        delta2 = torch.empty_like(X).uniform_(-self.eps, self.eps)
        delta1.requires_grad = True
        delta2.requires_grad = True
        
        X_new[:len(X)] += delta1
        X_new[len(X):] += delta2
        X_new = torch.clamp(X_new, 0, 1)
        
        pre = self.model(X_new)
        loss = nn.CrossEntropyLoss()(pre, Y_new)

        grad1, grad2 = torch.autograd.grad(loss, [delta1, delta2],
                                           create_graph=True)
        
        X_adv = X_new[:len(X)] + self.alpha*grad1.data.sign()
        delta = torch.clamp(X_adv - X, min=-self.eps, max=self.eps).detach()
        X_adv = torch.clamp(X + delta, min=0, max=1).detach()
        
        grads_nnz_idx = ((grad1**2).sum([1, 2, 3])**0.5 != 0) * ((grad2**2).sum([1, 2, 3])**0.5 != 0)
        grad1, grad2 = grad1[grads_nnz_idx], grad2[grads_nnz_idx]
        grad1_norms = self._l2_norm_batch(grad1)
        grad2_norms = self._l2_norm_batch(grad2)
        grad1_normalized = grad1 / grad1_norms[:, None, None, None]
        grad2_normalized = grad2 / grad2_norms[:, None, None, None]
        cos = torch.sum(grad1_normalized * grad2_normalized, (1, 2, 3))

        gradalign_loss = torch.tensor([0]).to(self.device)
        if len(cos) > 0:
            cos_aggr = cos.mean()
            gradalign_loss = (1.0 - cos_aggr)
        
        pre = self.model(X_adv)
        ce_loss = nn.CrossEntropyLoss()(pre, Y)
        
        cost = ce_loss + self.grad_align_cos_lambda * gradalign_loss
        
        self.optimizer.zero_grad()
        cost.backward()
        self.optimizer.step()

        return cost.item(), ce_loss.item(), gradalign_loss.item()
    
    