# coding=utf-8
# Copyright 2023 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
""" Testing suite for the PyTorch Pop2Piano model. """

import copy
import tempfile
import unittest

from transformers import Pop2PianoConfig
from transformers.feature_extraction_utils import BatchFeature
from transformers.testing_utils import require_torch, slow, torch_device
from transformers.utils import is_torch_available

from ...generation.test_utils import GenerationTesterMixin
from ...test_configuration_common import ConfigTester
from ...test_modeling_common import ModelTesterMixin, ids_tensor


if is_torch_available():
    import torch

    from transformers import Pop2PianoForConditionalGeneration
    from transformers.models.pop2piano.modeling_pop2piano import POP2PIANO_PRETRAINED_MODEL_ARCHIVE_LIST


@require_torch
class Pop2PianoModelTester:
    def __init__(
        self,
        parent,
        vocab_size=99,
        batch_size=13,
        encoder_seq_length=7,
        decoder_seq_length=9,
        # For common tests
        is_training=False,
        use_attention_mask=True,
        use_labels=True,
        hidden_size=512,
        num_hidden_layers=5,
        num_attention_heads=4,
        d_ff=37,
        relative_attention_num_buckets=8,
        dropout_rate=0.1,
        initializer_factor=0.002,
        eos_token_id=1,
        pad_token_id=0,
        decoder_start_token_id=0,
        scope=None,
        decoder_layers=None,
    ):
        self.parent = parent
        self.batch_size = batch_size
        self.encoder_seq_length = encoder_seq_length
        self.decoder_seq_length = decoder_seq_length
        # For common tests
        self.seq_length = self.decoder_seq_length
        self.is_training = is_training
        self.use_attention_mask = use_attention_mask
        self.use_labels = use_labels
        self.vocab_size = vocab_size
        self.hidden_size = hidden_size
        self.num_hidden_layers = num_hidden_layers
        self.num_attention_heads = num_attention_heads
        self.d_ff = d_ff
        self.relative_attention_num_buckets = relative_attention_num_buckets
        self.dropout_rate = dropout_rate
        self.initializer_factor = initializer_factor
        self.eos_token_id = eos_token_id
        self.pad_token_id = pad_token_id
        self.decoder_start_token_id = decoder_start_token_id
        self.scope = None
        self.decoder_layers = decoder_layers

    def prepare_config_and_inputs(self):
        input_ids = ids_tensor([self.batch_size, self.encoder_seq_length], self.vocab_size)
        decoder_input_ids = ids_tensor([self.batch_size, self.decoder_seq_length], self.vocab_size)

        attention_mask = None
        decoder_attention_mask = None
        if self.use_attention_mask:
            attention_mask = ids_tensor([self.batch_size, self.encoder_seq_length], vocab_size=2)
            decoder_attention_mask = ids_tensor([self.batch_size, self.decoder_seq_length], vocab_size=2)

        lm_labels = None
        if self.use_labels:
            lm_labels = ids_tensor([self.batch_size, self.decoder_seq_length], self.vocab_size)

        config = self.get_config()

        return (
            config,
            input_ids,
            decoder_input_ids,
            attention_mask,
            decoder_attention_mask,
            lm_labels,
        )

    def get_pipeline_config(self):
        return Pop2PianoConfig(
            vocab_size=166,  # Pop2Piano forces 100 extra tokens
            d_model=self.hidden_size,
            d_ff=self.d_ff,
            d_kv=self.hidden_size // self.num_attention_heads,
            num_layers=self.num_hidden_layers,
            num_decoder_layers=self.decoder_layers,
            num_heads=self.num_attention_heads,
            relative_attention_num_buckets=self.relative_attention_num_buckets,
            dropout_rate=self.dropout_rate,
            initializer_factor=self.initializer_factor,
            eos_token_id=self.eos_token_id,
            bos_token_id=self.pad_token_id,
            pad_token_id=self.pad_token_id,
            decoder_start_token_id=self.decoder_start_token_id,
        )

    def get_config(self):
        return Pop2PianoConfig(
            vocab_size=self.vocab_size,
            d_model=self.hidden_size,
            d_ff=self.d_ff,
            d_kv=self.hidden_size // self.num_attention_heads,
            num_layers=self.num_hidden_layers,
            num_decoder_layers=self.decoder_layers,
            num_heads=self.num_attention_heads,
            relative_attention_num_buckets=self.relative_attention_num_buckets,
            dropout_rate=self.dropout_rate,
            initializer_factor=self.initializer_factor,
            eos_token_id=self.eos_token_id,
            bos_token_id=self.pad_token_id,
            pad_token_id=self.pad_token_id,
            decoder_start_token_id=self.decoder_start_token_id,
        )

    def check_prepare_lm_labels_via_shift_left(
        self,
        config,
        input_ids,
        decoder_input_ids,
        attention_mask,
        decoder_attention_mask,
        lm_labels,
    ):
        model = Pop2PianoForConditionalGeneration(config=config)
        model.to(torch_device)
        model.eval()

        # make sure that lm_labels are correctly padded from the right
        lm_labels.masked_fill_((lm_labels == self.decoder_start_token_id), self.eos_token_id)

        # add causal pad token mask
        triangular_mask = torch.tril(lm_labels.new_ones(lm_labels.shape)).logical_not()
        lm_labels.masked_fill_(triangular_mask, self.pad_token_id)
        decoder_input_ids = model._shift_right(lm_labels)

        for i, (decoder_input_ids_slice, lm_labels_slice) in enumerate(zip(decoder_input_ids, lm_labels)):
            # first item
            self.parent.assertEqual(decoder_input_ids_slice[0].item(), self.decoder_start_token_id)
            if i < decoder_input_ids_slice.shape[-1]:
                if i < decoder_input_ids.shape[-1] - 1:
                    # items before diagonal
                    self.parent.assertListEqual(
                        decoder_input_ids_slice[1 : i + 1].tolist(), lm_labels_slice[:i].tolist()
                    )
                # pad items after diagonal
                if i < decoder_input_ids.shape[-1] - 2:
                    self.parent.assertListEqual(
                        decoder_input_ids_slice[i + 2 :].tolist(), lm_labels_slice[i + 1 : -1].tolist()
                    )
            else:
                # all items after square
                self.parent.assertListEqual(decoder_input_ids_slice[1:].tolist(), lm_labels_slice[:-1].tolist())

    def create_and_check_model(
        self,
        config,
        input_ids,
        decoder_input_ids,
        attention_mask,
        decoder_attention_mask,
        lm_labels,
    ):
        model = Pop2PianoForConditionalGeneration(config=config)
        model.to(torch_device)
        model.eval()
        result = model(
            input_ids=input_ids,
            decoder_input_ids=decoder_input_ids,
            attention_mask=attention_mask,
            decoder_attention_mask=decoder_attention_mask,
        )
        result = model(input_ids=input_ids, decoder_input_ids=decoder_input_ids)
        decoder_past = result.past_key_values
        encoder_output = result.encoder_last_hidden_state

        self.parent.assertEqual(encoder_output.size(), (self.batch_size, self.encoder_seq_length, self.hidden_size))
        # There should be `num_layers` key value embeddings stored in decoder_past
        self.parent.assertEqual(len(decoder_past), config.num_layers)
        # There should be a self attn key, a self attn value, a cross attn key and a cross attn value stored in each decoder_past tuple
        self.parent.assertEqual(len(decoder_past[0]), 4)

    def create_and_check_with_lm_head(
        self,
        config,
        input_ids,
        decoder_input_ids,
        attention_mask,
        decoder_attention_mask,
        lm_labels,
    ):
        model = Pop2PianoForConditionalGeneration(config=config).to(torch_device).eval()
        outputs = model(
            input_ids=input_ids,
            decoder_input_ids=decoder_input_ids,
            decoder_attention_mask=decoder_attention_mask,
            labels=lm_labels,
        )
        self.parent.assertEqual(len(outputs), 4)
        self.parent.assertEqual(outputs["logits"].size(), (self.batch_size, self.decoder_seq_length, self.vocab_size))
        self.parent.assertEqual(outputs["loss"].size(), ())

    def create_and_check_decoder_model_past(
        self,
        config,
        input_ids,
        decoder_input_ids,
        attention_mask,
        decoder_attention_mask,
        lm_labels,
    ):
        model = Pop2PianoForConditionalGeneration(config=config).get_decoder().to(torch_device).eval()
        # first forward pass
        outputs = model(input_ids, use_cache=True)
        outputs_use_cache_conf = model(input_ids)
        outputs_no_past = model(input_ids, use_cache=False)

        self.parent.assertTrue(len(outputs) == len(outputs_use_cache_conf))
        self.parent.assertTrue(len(outputs) == len(outputs_no_past) + 1)

        output, past_key_values = outputs.to_tuple()

        # create hypothetical next token and extent to next_input_ids
        next_tokens = ids_tensor((self.batch_size, 1), config.vocab_size)

        # append to next input_ids and
        next_input_ids = torch.cat([input_ids, next_tokens], dim=-1)

        output_from_no_past = model(next_input_ids)["last_hidden_state"]
        output_from_past = model(next_tokens, past_key_values=past_key_values)["last_hidden_state"]

        # select random slice
        random_slice_idx = ids_tensor((1,), output_from_past.shape[-1]).item()
        output_from_no_past_slice = output_from_no_past[:, -1, random_slice_idx].detach()
        output_from_past_slice = output_from_past[:, 0, random_slice_idx].detach()

        # test that outputs are equal for slice
        self.parent.assertTrue(torch.allclose(output_from_past_slice, output_from_no_past_slice, atol=1e-3))

    def create_and_check_decoder_model_attention_mask_past(
        self,
        config,
        input_ids,
        decoder_input_ids,
        attention_mask,
        decoder_attention_mask,
        lm_labels,
    ):
        model = Pop2PianoForConditionalGeneration(config=config).get_decoder()
        model.to(torch_device)
        model.eval()

        # create attention mask
        attn_mask = torch.ones(input_ids.shape, dtype=torch.long, device=torch_device)

        half_seq_length = input_ids.shape[-1] // 2
        attn_mask[:, half_seq_length:] = 0

        # first forward pass
        output, past_key_values = model(input_ids, attention_mask=attn_mask, use_cache=True).to_tuple()

        # create hypothetical next token and extent to next_input_ids
        next_tokens = ids_tensor((self.batch_size, 1), config.vocab_size)

        # change a random masked slice from input_ids
        random_seq_idx_to_change = ids_tensor((1,), half_seq_length).item() + 1
        random_other_next_tokens = ids_tensor((self.batch_size, 1), config.vocab_size).squeeze(-1)
        input_ids[:, -random_seq_idx_to_change] = random_other_next_tokens

        # append to next input_ids and attn_mask
        next_input_ids = torch.cat([input_ids, next_tokens], dim=-1)
        attn_mask = torch.cat(
            [attn_mask, torch.ones((attn_mask.shape[0], 1), dtype=torch.long, device=torch_device)],
            dim=1,
        )

        # get two different outputs
        output_from_no_past = model(next_input_ids, attention_mask=attn_mask)["last_hidden_state"]
        output_from_past = model(next_tokens, past_key_values=past_key_values, attention_mask=attn_mask)[
            "last_hidden_state"
        ]

        # select random slice
        random_slice_idx = ids_tensor((1,), output_from_past.shape[-1]).item()
        output_from_no_past_slice = output_from_no_past[:, -1, random_slice_idx].detach()
        output_from_past_slice = output_from_past[:, 0, random_slice_idx].detach()

        # test that outputs are equal for slice
        self.parent.assertTrue(torch.allclose(output_from_past_slice, output_from_no_past_slice, atol=1e-3))

    def create_and_check_decoder_model_past_large_inputs(
        self,
        config,
        input_ids,
        decoder_input_ids,
        attention_mask,
        decoder_attention_mask,
        lm_labels,
    ):
        model = Pop2PianoForConditionalGeneration(config=config).get_decoder().to(torch_device).eval()
        # first forward pass
        outputs = model(input_ids, attention_mask=attention_mask, use_cache=True)

        output, past_key_values = outputs.to_tuple()

        # create hypothetical multiple next token and extent to next_input_ids
        next_tokens = ids_tensor((self.batch_size, 3), config.vocab_size)
        next_mask = ids_tensor((self.batch_size, 3), vocab_size=2)

        # append to next input_ids and
        next_input_ids = torch.cat([input_ids, next_tokens], dim=-1)
        next_attention_mask = torch.cat([attention_mask, next_mask], dim=-1)

        output_from_no_past = model(next_input_ids, attention_mask=next_attention_mask)["last_hidden_state"]
        output_from_past = model(next_tokens, attention_mask=next_attention_mask, past_key_values=past_key_values)[
            "last_hidden_state"
        ]

        # select random slice
        random_slice_idx = ids_tensor((1,), output_from_past.shape[-1]).item()
        output_from_no_past_slice = output_from_no_past[:, -3:, random_slice_idx].detach()
        output_from_past_slice = output_from_past[:, :, random_slice_idx].detach()

        self.parent.assertTrue(output_from_past_slice.shape[1] == next_tokens.shape[1])

        # test that outputs are equal for slice
        self.parent.assertTrue(torch.allclose(output_from_past_slice, output_from_no_past_slice, atol=1e-3))

    def create_and_check_generate_with_past_key_values(
        self,
        config,
        input_ids,
        decoder_input_ids,
        attention_mask,
        decoder_attention_mask,
        lm_labels,
    ):
        model = Pop2PianoForConditionalGeneration(config=config).to(torch_device).eval()
        torch.manual_seed(0)
        output_without_past_cache = model.generate(
            input_ids[:1], num_beams=2, max_length=5, do_sample=True, use_cache=False
        )
        torch.manual_seed(0)
        output_with_past_cache = model.generate(input_ids[:1], num_beams=2, max_length=5, do_sample=True)
        self.parent.assertTrue(torch.all(output_with_past_cache == output_without_past_cache))

    def create_and_check_model_fp16_forward(
        self,
        config,
        input_ids,
        decoder_input_ids,
        attention_mask,
        decoder_attention_mask,
        lm_labels,
    ):
        model = Pop2PianoForConditionalGeneration(config=config).to(torch_device).half().eval()
        output = model(input_ids, decoder_input_ids=input_ids, attention_mask=attention_mask)[
            "encoder_last_hidden_state"
        ]
        self.parent.assertFalse(torch.isnan(output).any().item())

    def create_and_check_encoder_decoder_shared_weights(
        self,
        config,
        input_ids,
        decoder_input_ids,
        attention_mask,
        decoder_attention_mask,
        lm_labels,
    ):
        for model_class in [Pop2PianoForConditionalGeneration]:
            torch.manual_seed(0)
            model = model_class(config=config).to(torch_device).eval()
            # load state dict copies weights but does not tie them
            model.encoder.load_state_dict(model.decoder.state_dict(), strict=False)

            torch.manual_seed(0)
            tied_config = copy.deepcopy(config)
            tied_config.tie_encoder_decoder = True
            tied_model = model_class(config=tied_config).to(torch_device).eval()

            model_result = model(
                input_ids=input_ids,
                decoder_input_ids=decoder_input_ids,
                attention_mask=attention_mask,
                decoder_attention_mask=decoder_attention_mask,
            )

            tied_model_result = tied_model(
                input_ids=input_ids,
                decoder_input_ids=decoder_input_ids,
                attention_mask=attention_mask,
                decoder_attention_mask=decoder_attention_mask,
            )

            # check that models has less parameters
            self.parent.assertLess(
                sum(p.numel() for p in tied_model.parameters()), sum(p.numel() for p in model.parameters())
            )
            random_slice_idx = ids_tensor((1,), model_result[0].shape[-1]).item()

            # check that outputs are equal
            self.parent.assertTrue(
                torch.allclose(
                    model_result[0][0, :, random_slice_idx], tied_model_result[0][0, :, random_slice_idx], atol=1e-4
                )
            )

            # check that outputs after saving and loading are equal
            with tempfile.TemporaryDirectory() as tmpdirname:
                tied_model.save_pretrained(tmpdirname)
                tied_model = model_class.from_pretrained(tmpdirname)
                tied_model.to(torch_device)
                tied_model.eval()

                # check that models has less parameters
                self.parent.assertLess(
                    sum(p.numel() for p in tied_model.parameters()), sum(p.numel() for p in model.parameters())
                )
                random_slice_idx = ids_tensor((1,), model_result[0].shape[-1]).item()

                tied_model_result = tied_model(
                    input_ids=input_ids,
                    decoder_input_ids=decoder_input_ids,
                    attention_mask=attention_mask,
                    decoder_attention_mask=decoder_attention_mask,
                )

                # check that outputs are equal
                self.parent.assertTrue(
                    torch.allclose(
                        model_result[0][0, :, random_slice_idx],
                        tied_model_result[0][0, :, random_slice_idx],
                        atol=1e-4,
                    )
                )

    def check_resize_embeddings_Pop2Piano_v1_1(
        self,
        config,
    ):
        prev_vocab_size = config.vocab_size

        config.tie_word_embeddings = False
        model = Pop2PianoForConditionalGeneration(config=config).to(torch_device).eval()
        model.resize_token_embeddings(prev_vocab_size - 10)

        self.parent.assertEqual(model.get_input_embeddings().weight.shape[0], prev_vocab_size - 10)
        self.parent.assertEqual(model.get_output_embeddings().weight.shape[0], prev_vocab_size - 10)
        self.parent.assertEqual(model.config.vocab_size, prev_vocab_size - 10)

    def prepare_config_and_inputs_for_common(self):
        config_and_inputs = self.prepare_config_and_inputs()
        (
            config,
            input_ids,
            decoder_input_ids,
            attention_mask,
            decoder_attention_mask,
            lm_labels,
        ) = config_and_inputs

        inputs_dict = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "decoder_input_ids": decoder_input_ids,
            "decoder_attention_mask": decoder_attention_mask,
            "use_cache": False,
        }
        return config, inputs_dict


@require_torch
class Pop2PianoModelTest(ModelTesterMixin, GenerationTesterMixin, unittest.TestCase):
    all_model_classes = (Pop2PianoForConditionalGeneration,) if is_torch_available() else ()
    all_generative_model_classes = ()
    all_parallelizable_model_classes = ()
    fx_compatible = False
    test_pruning = False
    test_resize_embeddings = True
    test_model_parallel = False
    is_encoder_decoder = True

    def setUp(self):
        self.model_tester = Pop2PianoModelTester(self)
        self.config_tester = ConfigTester(self, config_class=Pop2PianoConfig, d_model=37)

    def test_config(self):
        self.config_tester.run_common_tests()

    def test_shift_right(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.check_prepare_lm_labels_via_shift_left(*config_and_inputs)

    def test_model(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.create_and_check_model(*config_and_inputs)

    def test_model_v1_1(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        # check that gated gelu feed forward and different word embeddings work
        config = config_and_inputs[0]
        config.tie_word_embeddings = False
        config.feed_forward_proj = "gated-gelu"
        self.model_tester.create_and_check_model(config, *config_and_inputs[1:])

    def test_config_and_model_silu_gated(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        config = config_and_inputs[0]
        config.feed_forward_proj = "gated-silu"
        self.model_tester.create_and_check_model(*config_and_inputs)

    def test_with_lm_head(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.create_and_check_with_lm_head(*config_and_inputs)

    def test_decoder_model_past(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.create_and_check_decoder_model_past(*config_and_inputs)

    def test_decoder_model_past_with_attn_mask(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.create_and_check_decoder_model_attention_mask_past(*config_and_inputs)

    def test_decoder_model_past_with_3d_attn_mask(self):
        (
            config,
            input_ids,
            decoder_input_ids,
            attention_mask,
            decoder_attention_mask,
            lm_labels,
        ) = self.model_tester.prepare_config_and_inputs()

        attention_mask = ids_tensor(
            [self.model_tester.batch_size, self.model_tester.encoder_seq_length, self.model_tester.encoder_seq_length],
            vocab_size=2,
        )
        decoder_attention_mask = ids_tensor(
            [self.model_tester.batch_size, self.model_tester.decoder_seq_length, self.model_tester.decoder_seq_length],
            vocab_size=2,
        )

        self.model_tester.create_and_check_decoder_model_attention_mask_past(
            config,
            input_ids,
            decoder_input_ids,
            attention_mask,
            decoder_attention_mask,
            lm_labels,
        )

    def test_decoder_model_past_with_large_inputs(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.create_and_check_decoder_model_past_large_inputs(*config_and_inputs)

    def test_encoder_decoder_shared_weights(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.create_and_check_encoder_decoder_shared_weights(*config_and_inputs)

    @unittest.skipIf(torch_device == "cpu", "Cant do half precision")
    def test_model_fp16_forward(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        self.model_tester.create_and_check_model_fp16_forward(*config_and_inputs)

    def test_v1_1_resize_embeddings(self):
        config = self.model_tester.prepare_config_and_inputs()[0]
        self.model_tester.check_resize_embeddings_Pop2Piano_v1_1(config)

    @slow
    def test_model_from_pretrained(self):
        for model_name in POP2PIANO_PRETRAINED_MODEL_ARCHIVE_LIST[:1]:
            model = Pop2PianoForConditionalGeneration.from_pretrained(model_name)
            self.assertIsNotNone(model)

    @unittest.skip("Test has a segmentation fault on torch 1.8.0")
    def test_export_to_onnx(self):
        config_and_inputs = self.model_tester.prepare_config_and_inputs()
        model = Pop2PianoForConditionalGeneration(config_and_inputs[0]).to(torch_device)
        with tempfile.TemporaryDirectory() as tmpdirname:
            torch.onnx.export(
                model,
                (config_and_inputs[1], config_and_inputs[3], config_and_inputs[2]),
                f"{tmpdirname}/Pop2Piano_test.onnx",
                export_params=True,
                opset_version=9,
                input_names=["input_ids", "decoder_input_ids"],
            )

    @unittest.skip("Does not work on the tiny model as we keep hitting edge cases.")
    def test_disk_offload(self):
        pass

    @unittest.skip("Pop2Piano does not need this test.")
    def test_generate_with_head_masking(self):
        pass

    @unittest.skip("Pop2Piano does not need this test.")
    def test_generate_with_past_key_values(self):
        pass


@require_torch
class Pop2PianoModelIntegrationTests(unittest.TestCase):
    @slow
    def test_mel_conditioner_integration(self):
        composer = "composer1"
        model = Pop2PianoForConditionalGeneration.from_pretrained("susnato/pop2piano_dev")
        input_embeds = torch.ones([10, 100, 512])

        composer_value = model.config.composer_to_feature_token[composer]
        composer_value = torch.tensor(composer_value)
        composer_value = composer_value.repeat(input_embeds.size(0))
        outputs = model.mel_conditioner(input_embeds, composer_value)

        # check shape
        self.assertEqual(outputs.size(), torch.Size([10, 101, 512]))

        # check values
        self.assertEqual(
            outputs[0, :3, :3].detach().cpu().numpy().tolist(),
            [[1.0475305318832397, 0.29052114486694336, -0.47778210043907166], [1.0, 1.0, 1.0], [1.0, 1.0, 1.0]],
        )

    @slow
    def test_full_model_integration(self):
        model = Pop2PianoForConditionalGeneration.from_pretrained("susnato/pop2piano_dev")
        model.eval()
        input_features = BatchFeature({"input_features": torch.ones([75, 66, 512])})
        outputs = model.generate(input_features=input_features)

        # check for shapes
        self.assertEqual(outputs.size(0), 75)

        # check for values
        self.assertEqual(outputs[0, :3].detach().cpu().numpy().tolist(), [0, 1])