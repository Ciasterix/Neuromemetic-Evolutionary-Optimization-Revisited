import time

import deap
import numpy as np
import tensorflow as tf

from model.Decoder import Decoder
from model.Encoder import Encoder
from model.Population import Population
from model.Surrogate import Surrogate
from utils import create_expression_tree


class NeoOriginal:

    def __init__(  # TODO move parameters to config file
            self,
            pset,
            batch_size=64,
            max_size=100,
            vocab_inp_size=32,
            vocab_tar_size=32,
            embedding_dim=64,
            units=128,
            hidden_size=128,
            alpha=0.1,
            epochs=200,
            epoch_decay=1,
            min_epochs=10,
            verbose=True
    ):
        self.alpha = alpha
        self.batch_size = batch_size
        self.max_size = max_size
        self.epochs = epochs
        self.epoch_decay = epoch_decay
        self.min_epochs = min_epochs
        self.train_steps = 0

        self.verbose = verbose

        self.enc = Encoder(vocab_inp_size, embedding_dim, units, batch_size)
        self.dec = Decoder(vocab_inp_size, vocab_tar_size, embedding_dim, units, batch_size)
        self.surrogate = Surrogate(hidden_size)
        self.population = Population(pset, max_size, batch_size)
        self.prob = 0.5

        self.optimizer = tf.keras.optimizers.Adam()
        self.loss_object = tf.keras.losses.SparseCategoricalCrossentropy(
            from_logits=True, reduction='none')

    def save_models(self):
        self.enc.save_weights(
            "model/weights/encoder/enc_{}".format(self.train_steps),
            save_format="tf")
        self.dec.save_weights(
            "model/weights/decoder/dec_{}".format(self.train_steps),
            save_format="tf")
        self.surrogate.save_weights(
            "model/weights/surrogate/surrogate_{}".format(self.train_steps),
            save_format="tf")

    def load_models(self, train_steps):
        self.enc.load_weights(
            "model/weights/encoder/enc_{}".format(train_steps))
        self.dec.load_weights(
            "model/weights/decoder/dec_{}".format(train_steps))
        self.surrogate.load_weights(
            "model/weights/surrogate/surrogate_{}".format(train_steps))

    # @tf.function
    def train_step(self, inp, targ, targ_surrogate, enc_hidden,
                   enc_cell):
        autoencoder_loss = 0
        with tf.GradientTape(persistent=True) as tape:
            enc_output, enc_hidden, enc_cell = self.enc(
                inp, [enc_hidden, enc_cell])
            # enc_output, enc_hidden = self.enc(
            #     inp, enc_hidden)
            # print("enc", enc_output.shape, enc_hidden.shape)

            surrogate_output = self.surrogate(enc_hidden)
            surrogate_loss = self.surrogate_loss_function(targ_surrogate,
                                                          surrogate_output)

            dec_hidden = enc_hidden
            dec_cell = enc_cell
            context = tf.zeros(shape=[len(dec_hidden), 1, dec_hidden.shape[1]])
                    
            dec_input = tf.expand_dims([1] * len(inp),
                                       # [1] - starting token
                                       1)

            # Teacher forcing - feeding the target as the next input
            for t in range(1, self.max_size):
                initial_state = [dec_hidden, dec_cell]
                predictions, context, [dec_hidden, dec_cell], _ = self.dec(
                    dec_input, context, enc_output, initial_state)
                # print(tf.argmax(predictions, axis=1))
                autoencoder_loss += self.autoencoder_loss_function(targ[:, t],
                                                                   predictions)

                # using teacher forcing
                if tf.random.uniform(shape=[], maxval=1,
                                     dtype=tf.float32) > self.prob:
                    dec_input = tf.expand_dims(targ[:, t], 1)
                else:
                    dec_input = tf.expand_dims(tf.argmax(predictions, axis=1,
                                                         output_type=tf.dtypes.int32),
                                               1)
            # tf.print(autoencoder_loss, surrogate_loss)
            loss = autoencoder_loss + self.alpha * surrogate_loss
        # print("-" * 80)
        # print("AE loss:", autoencoder_loss.numpy())
        # print("Surrogate loss:", surrogate_loss.numpy())
        # print(targ.shape[1])
        batch_loss = (autoencoder_loss / int(targ.shape[1])) + self.alpha * surrogate_loss
        batch_ae_loss = (autoencoder_loss / int(targ.shape[1]))
        batch_surrogate_loss = surrogate_loss
        # self.enc.update(loss, tape)
        # self.dec.update(loss, tape)
        # self.surrogate.update(loss, tape)
        gradients, variables = self.backward(loss, tape)
        self.optimize(gradients, variables)
        # print("Koniec train stepa")

        return batch_loss, batch_ae_loss, batch_surrogate_loss

    def backward(self, loss, tape):
        variables = self.enc.trainable_variables + self.dec.trainable_variables + self.surrogate.trainable_variables
        gradients = tape.gradient(loss, variables)
        return gradients, variables

    def optimize(self, gradients, variables):
        self.optimizer.apply_gradients(zip(gradients, variables))

    def surrogate_breed(self, output, latent, tape):
        gradients = tape.gradient(output, latent)
        return gradients

    def update_latent(self, latent, gradients, eta):
        latent += eta * gradients
        return latent

    def autoencoder_loss_function(self, real, pred):
        mask = tf.math.logical_not(tf.math.equal(real, 0))
        loss_ = self.loss_object(real, pred)
        mask = tf.cast(mask, dtype=loss_.dtype)
        loss_ *= mask
        return tf.reduce_mean(loss_)

    def surrogate_loss_function(self, real, pred):
        loss_ = tf.keras.losses.mean_squared_error(real, pred)
        return tf.reduce_mean(loss_)

    def __train(self):

        for epoch in range(self.epochs):
            self.epoch = epoch
            start = time.time()

            total_loss = 0
            total_ae_loss = 0
            total_surrogate_loss = 0

            data_generator = self.population()
            for (batch, (inp, targ, targ_surrogate)) in enumerate(
                    data_generator):
                # print("Batch:", batch)
                enc_hidden = self.enc.initialize_hidden_state(batch_sz=len(inp))
                enc_cell = self.enc.initialize_cell_state(batch_sz=len(inp))
                batch_loss, batch_ae_loss, batch_surrogate_loss = self.train_step(inp, targ,
                                                                                  targ_surrogate,
                                                                                  enc_hidden,
                                                                                  enc_cell)
                total_loss += batch_loss
                total_ae_loss += batch_ae_loss
                total_surrogate_loss += batch_surrogate_loss

                if False and batch % 1 == 0 and self.verbose:
                    print(
                        'Epoch {} Batch {} Loss {:.4f}'.format(epoch + 1, batch,
                                                               batch_loss.numpy()))

            if self.verbose:
                epoch_loss = total_loss / self.population.steps_per_epoch
                epoch_ae_loss = total_ae_loss / self.population.steps_per_epoch
                epoch_surrogate_loss = total_surrogate_loss / self.population.steps_per_epoch
                # print('Epoch {} Loss {:.6f} Time: {:.3f}'.format(
                #     epoch + 1, epoch_loss, time.time() - start))
                print('Epoch {} Loss {:.6f} AE_loss {:.6f} Surrogate_loss {:.6f} Time: {:.3f}'.format(
                    epoch + 1, epoch_loss, epoch_ae_loss, epoch_surrogate_loss, time.time() - start))

        # decrease number of epoch, but don't go below self.min_epochs
        self.epochs = max(self.epochs - self.epoch_decay, self.min_epochs)

    def _gen_childs(self, candidates, enc_output, enc_hidden, enc_cell, max_eta=1000):
        children = []
        eta = 0
        enc_mask = enc_output._keras_mask
        while eta < max_eta:
            eta += 1
            start = time.time()
            new_children = self._gen_decoded(eta, enc_output, enc_hidden, enc_cell, enc_mask).numpy()
            new_children = self.cut_seq(new_children, end_token=2)
            new_ind, copy_ind = self.find_new(new_children, candidates)
            print("Eta {} Not-changed {} Time: {:.3f}".format(
                eta, len(copy_ind), time.time() - start))
            for i in new_ind:
                children.append(new_children[i])
            if len(copy_ind) < 1:
                break
            enc_output = tf.gather(enc_output, copy_ind)
            enc_mask = tf.gather(enc_mask, copy_ind)
            enc_hidden = tf.gather(enc_hidden, copy_ind)
            enc_cell = tf.gather(enc_cell, copy_ind)
            candidates = tf.gather(candidates, copy_ind)
        if eta == max_eta:
            print("Maximal value of eta reached - breed stopped")
        for i in copy_ind:
            children.append(new_children[i])
        return children

    def _gen_decoded(self, eta, enc_output, enc_hidden, enc_cell, enc_mask):
        with tf.GradientTape(persistent=True, watch_accessed_variables=False) as tape:
            tape.watch(enc_hidden)
            # tape.watch(enc_output)
            surrogate_output = self.surrogate(enc_hidden)
            # print("enc_hidden shape", enc_hidden.shape)
            # surrogate_output = 2 * enc_hidden
        # print(surrogate_output)
        gradients = self.surrogate_breed(surrogate_output, enc_hidden,
                                         tape)
        dec_hidden = self.update_latent(enc_hidden, gradients, eta=eta)
        dec_cell = enc_cell
        context = tf.zeros(shape=[len(dec_hidden), 1, dec_hidden.shape[1]])

        dec_input = tf.expand_dims([1] * len(enc_hidden),  # [1] - start token
                                   1)
        child = dec_input
        for t in range(1, self.max_size - 1):
            initial_state = [dec_hidden, dec_cell]
            predictions, context, [dec_hidden, dec_cell], _ = self.dec(
                dec_input, context, enc_output, initial_state, enc_mask)
            dec_input = tf.expand_dims(
                tf.argmax(predictions, axis=1, output_type=tf.dtypes.int32), 1)
            child = tf.concat([child, dec_input], axis=1)
        stop_tokens = tf.expand_dims([2] * len(enc_hidden), 1)
        child = tf.concat([child,
                           stop_tokens], axis=1)
        return child

    def cut_seq(self, seq, end_token=2):
        ind = (seq == end_token).argmax(1)
        # res = [np.pad(d[:i + 1], (0, self.max_size - i - 1)) for d, i in zip(seq, ind)]
        res = []
        for d, i in zip(seq, ind):
            repaired_tree = create_expression_tree(d[:i + 1][1:-1])
            repaired_seq = [i.data for i in repaired_tree.preorder()][
                           -(self.max_size - 2):]
            repaired_seq = [1] + repaired_seq + [2]
            res.append(np.pad(repaired_seq, (0, self.max_size - i - 1)))
        return res

    def find_new(self, seq, candidates):
        new_ind = []
        copy_ind = []
        n = False
        cp = False
        for i, (s, c) in enumerate(zip(seq, candidates)):
            if not np.array_equal(s, c):
                if not n:
                    # print("S:", s, "C", c)
                    n = True
                new_ind.append(i)
            else:
                if not cp:
                    # print("S:", s, "C", c)
                    cp = True
                copy_ind.append(i)
        return new_ind, copy_ind

    def _gen_latent(self, candidates):
        enc_hidden = self.enc.initialize_hidden_state(batch_sz=len(candidates))
        enc_cell = self.enc.initialize_cell_state(batch_sz=len(candidates))
        enc_output, enc_hidden, enc_cell = self.enc(candidates,
                                                    [enc_hidden, enc_cell])
        # enc_output, enc_hidden = self.enc(candidates,
        #                                   enc_hidden)
        return enc_output, enc_hidden, enc_cell

    def update(self):
        print("Training")
        self.enc.train()
        self.dec.train()
        self.__train()
        self.save_models()
        # tf.saved_model.save(self.enc, "model/weights/enc_{}".format(self.train_steps))
        # tf.saved_model.save(self.dec, "model/weights/dec_{}".format(self.train_steps))
        self.train_steps += 1



    def breed(self):
        print("Breed")
        self.dec.eval()
        # Simulate population
        # print("First program before breed", self.population.samples[0])
        data_generator = self.population(
            batch_size=len(self.population.samples))
        tokenized_pop = []
        for (batch, (inp, _, _)) in enumerate(data_generator):
            enc_output, enc_hidden, enc_cell = self._gen_latent(inp)
            tokenized_pop += (self._gen_childs(inp, enc_output, enc_hidden, enc_cell))

        # print("First program after breed", tokenized_pop[0])
        cos1 = [self.population.tokenizer.reproduce_expression(tp) for tp in
                tokenized_pop]
        offspring = [deap.creator.Individual(tp) for tp in cos1]
        return offspring


if __name__ == "__main__":
    neo = NeoOriginal(epochs=15)
    neo.update()  # second call to check epoch decay
    neo.breed()
