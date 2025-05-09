"""Training script for diffusion models."""

from datetime import datetime
import wandb
import torch
from diffusers import AutoencoderKL, VQModel

from diffusion_models.config import parse_args
from diffusion_models.datasets.dataloader import setup_dataloader, create_attribute_dataloader
from diffusion_models.training_loop import train_loop
from diffusion_models.noise_schedulers.ddim_scheduler import create_ddim_scheduler
from diffusion_models.noise_schedulers.ddpm_scheduler import create_ddpm_scheduler
from ema_pytorch import EMA

from diffusers.optimization import get_cosine_schedule_with_warmup
from diffusion_models.utils.attribute_utils import (
    create_sample_attributes,
    create_multi_hot_attributes
)

from diffusion_models.config import parse_args
from diffusion_models.datasets.dataloader import setup_dataloader, create_attribute_dataloader
from diffusion_models.training_loop import train_loop
from diffusion_models.noise_schedulers.ddim_scheduler import create_ddim_scheduler
from diffusion_models.noise_schedulers.ddpm_scheduler import create_ddpm_scheduler
from diffusion_models.models.model_factory import ModelFactory


def main():
    # Parse command line arguments and get config
    config = parse_args()

    # Dynamically set cross_attention_dim
    if config.is_conditional:
        if config.conditioning_type == "combined":
            config.cross_attention_dim = [(config.attribute_embed_dim + config.num_segmentation_channels)] * 4
        elif config.conditioning_type == "attribute":
            config.cross_attention_dim = [config.attribute_embed_dim] * 4
        elif config.conditioning_type == "segmentation":
            config.cross_attention_dim = [config.num_segmentation_channels] * 4

    # Set device
    config.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"\nUsing device: {config.device}")

    # Print config
    print("=" * 80)
    print("Training Configuration:")
    for key, value in vars(config).items():
        print(f"\t{key}: {value}")
    print("=" * 80)

    # Create model and noise scheduler
    model, attribute_embedder, vae = None, None, None

    # Load model and any related components
    if config.model == "unet_notebook":
        from diffusion_models.models.unconditional.unet_notebook import create_model
        model = create_model(config)

    elif config.model == "unet_notebook_r1":
        from diffusion_models.models.unconditional.unet_notebook_r1 import create_model
        model = create_model(config)

    elif config.model == "unet_notebook_r2":
        from diffusion_models.models.unconditional.unet_notebook_r2 import create_model
        model = create_model(config)

    elif config.model == "unet_notebook_r3":
        from diffusion_models.models.unconditional.unet_notebook_r3 import create_model
        model = create_model(config)

    elif config.model == "unet_notebook_r4":
        from diffusion_models.models.unconditional.unet_notebook_r4 import create_model
        model = create_model(config)

    elif config.model == "unet_notebook_r5":
        from diffusion_models.models.unconditional.unet_notebook_r5 import create_model
        model = create_model(config)

    elif config.model in ["conditional_unet", "pc_unet_1"]:
        from diffusion_models.models.conditional.pc_unet_1 import create_model
        from diffusion_models.models.conditional.attribute_embedder import AttributeEmbedder
        model = create_model(config)
        attribute_embedder = AttributeEmbedder(
            input_dim=config.num_attributes,
            hidden_dim=256
        )

    elif config.model in ["latent_conditional_unet", "lc_unet_1"]:
        from diffusion_models.models.conditional.lc_unet_1 import create_model
        from diffusion_models.models.conditional.attribute_embedder import AttributeEmbedder
        model = create_model(config)
        vae = AutoencoderKL.from_pretrained(
            "stable-diffusion-v1-5/stable-diffusion-v1-5",
            subfolder="vae",
            torch_dtype=torch.float32
        )
        vae = vae.to(config.device)
        attribute_embedder = AttributeEmbedder(
            input_dim=config.num_attributes,
            hidden_dim=256
        )

    elif config.model == "lc_unet_2":
        from diffusion_models.models.conditional.lc_unet_2 import create_model
        from diffusion_models.models.conditional.attribute_embedder import AttributeEmbedder
        model = create_model(config)
        vae = VQModel.from_pretrained(
            "CompVis/ldm-celebahq-256",
            subfolder="vqvae",
            torch_dtype=torch.float32
        )
        vae = vae.to(config.device)
        vae.eval()
        vae.requires_grad_(False)
        attribute_embedder = AttributeEmbedder(
            input_dim=config.num_attributes,
            hidden_dim=128
        )

    elif config.model == "lc_unet_3_vqvae":
        from diffusion_models.models.conditional.lc_unet_3_vqvae import create_model
        from diffusion_models.models.conditional.attribute_embedder import AttributeEmbedder
        model = create_model(config)
        vae = VQModel.from_pretrained(
            "CompVis/ldm-celebahq-256",
            subfolder="vqvae",
            torch_dtype=torch.float32
        )
        vae = vae.to(config.device)
        vae.eval()
        vae.requires_grad_(False)
        attribute_embedder = AttributeEmbedder(
            input_dim=config.num_attributes,
            num_layers=3,
            hidden_dim=256
        )

    elif config.model == "lc_unet_4_vqvae":
        from diffusion_models.models.conditional.lc_unet_4_vqvae import create_model
        from diffusion_models.models.conditional.attribute_embedder import AttributeEmbedder
        model = create_model(config)
        vae = VQModel.from_pretrained(
            "CompVis/ldm-celebahq-256",
            subfolder="vqvae",
            torch_dtype=torch.float32
        )
        vae = vae.to(config.device)
        vae.eval()
        vae.requires_grad_(False)
        attribute_embedder = AttributeEmbedder(
            input_dim=config.num_attributes,
            num_layers=3,
            hidden_dim=256
        )

    else:
        raise ValueError(f"Invalid model type: {config.model}")

 # Setup Exponential Moving Average
    ema = EMA(model, beta=0.9999, update_after_step=0, update_every=1) if config.use_ema else None

    if config.use_wandb:
        wandb.finish()
        wandb.init(
            entity=config.wandb_entity,
            project=config.wandb_project,
            name=config.run_name,
            config=config,
        )
        wandb.run.log_code(
            root=".",
            include_fn=lambda path: (
                path.endswith(".py") or path.endswith(".ipynb") or path.endswith(".sh")
            ),
            exclude_fn=lambda path: (
                ".venv" in path
                or ".git" in path
                or "checkpoints/" in path
                or "outputs/" in path
                or "data/" in path
            )
        )

    # Setup training dataset and preprocessing
    if config.is_conditional:
        train_dataloader = create_attribute_dataloader(
            image_dir=config.train_dir,
            attribute_label_path=config.attribute_file,
            segmentation_dir=config.segmentation_dir,
            batch_size=config.train_batch_size,
            num_workers=config.num_workers,
            shuffle=True,
            image_size=config.image_size
        )
        preprocess = train_dataloader.dataset.transform
    else:
        train_dataloader, preprocess = setup_dataloader(
            data_source=config.train_dir,
            batch_size=config.train_batch_size,
            image_size=config.image_size,
            shuffle=True
        )

    # Setup validation dataset
    val_dataloader = None
    if config.val_dir:
        if config.is_conditional:
            val_dataloader = create_attribute_dataloader(
                image_dir=config.val_dir,
                attribute_label_path=config.attribute_file,
                segmentation_dir=config.segmentation_dir,
                batch_size=config.eval_batch_size,
                num_workers=config.num_workers,
                shuffle=False,
                image_size=config.image_size
            )
        else:
            val_dataloader, _ = setup_dataloader(
                data_source=config.val_dir,
                batch_size=config.eval_batch_size,
                image_size=config.image_size,
                shuffle=False
            )
    else:
        print("[Warning] No validation directory provided, skipping validation during training.")

    # Create noise scheduler
    if config.scheduler_type == "ddim":
        noise_scheduler = create_ddim_scheduler(num_train_timesteps=config.num_train_timesteps)
        print("\nUsing DDIM scheduler for training")
    elif config.scheduler_type == "ddpm":
        noise_scheduler = create_ddpm_scheduler(num_train_timesteps=config.num_train_timesteps)
    else:
        raise ValueError(f"Unknown scheduler type: {config.scheduler_type}")

    # Optimizer and LR Scheduler
    params_to_optimize = model.parameters()
    optimizer = torch.optim.AdamW(
        params=params_to_optimize,
        lr=config.learning_rate,
        weight_decay=config.weight_decay
    )
    lr_scheduler = get_cosine_schedule_with_warmup(
        optimizer=optimizer,
        num_warmup_steps=config.lr_warmup_steps,
        num_training_steps=(len(train_dataloader) * config.num_epochs)
    )

    # Grid and val attribute samples
    grid_attributes = create_multi_hot_attributes(
        attribute_indices=config.grid_attribute_indices,
        num_attributes=config.num_attributes,
        num_samples=config.grid_num_samples,
        random_remaining_indices=config.grid_sample_random_remaining_indices
    ).to(config.device) if config.grid_attribute_indices is not None else None

    val_attributes = create_sample_attributes(
        num_samples=config.val_n_samples,
        num_attributes=config.num_attributes
    ).to(config.device) if val_dataloader is not None else None

    if val_attributes is not None:
        print("val_attributes shape: ", val_attributes.shape)
    if grid_attributes is not None:
        print("grid_attributes shape: ", grid_attributes.shape)
        print("grid_attributes first item: ", grid_attributes[0])

    # Start training
    train_loop(
        config=config,
        model=model,
        noise_scheduler=noise_scheduler,
        optimizer=optimizer,
        train_dataloader=train_dataloader,
        lr_scheduler=lr_scheduler,
        val_dataloader=val_dataloader,
        preprocess=preprocess,
        is_conditional=config.is_conditional,
        grid_attributes=grid_attributes,
        val_attributes=val_attributes,
        attribute_embedder=attribute_embedder,
        vae=vae,
        ema=ema
    )

    if config.use_wandb:
        wandb.finish()


if __name__ == "__main__":
    main()
