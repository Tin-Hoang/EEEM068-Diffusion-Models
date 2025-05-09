"""Evaluation metrics for diffusion models."""

import torch
from torchmetrics.image.fid import FrechetInceptionDistance
from torch.utils.data import DataLoader
from diffusers import DDPMPipeline, DiffusionPipeline
from typing import Optional, Union
from datasets import load_dataset
from torchvision import transforms
import os


def generate_and_calculate_fid(
    pipeline: DDPMPipeline,
    val_dataloader: DataLoader,
    device: torch.device,
    preprocess,
    num_train_timesteps: int,
    num_samples: Optional[int] = 50
) -> float:
    """Calculate FID score between generated samples and validation set.
    
    Args:
        pipeline: The DDPM pipeline for generating images
        val_dataloader: DataLoader for validation set
        device: Device to run calculation on
        preprocess: Preprocessing transform for the images
        num_train_timesteps: Number of timesteps for inference
        num_samples: Number of samples to generate (defaults to 50)
    
    Returns:
        FID score
    """
    fid = FrechetInceptionDistance(normalize=True).to(device)
    
    # If num_samples not specified, use validation set size
    if num_samples is None:
        num_samples = len(val_dataloader.dataset)
    
    with torch.no_grad():
        # Generate images using pipeline
        remaining_samples = num_samples
        while remaining_samples > 0:
            # Use a default batch size of 16 if val_dataloader.batch_size is None
            val_batch_size = val_dataloader.batch_size or 16
            batch_size = min(val_batch_size, remaining_samples)
            # Generate images using the pipeline
            samples = pipeline(
                batch_size=batch_size,
                generator=torch.manual_seed(42),
                num_inference_steps=num_train_timesteps
            ).images
            
            # Convert PIL images to tensors and normalize to [-1, 1]
            sample_tensors = torch.stack([
                preprocess(image) for image in samples
            ]).to(device)
            
            # Convert from [-1, 1] to [0, 1] range for FID
            sample_tensors = (sample_tensors * 0.5 + 0.5).clamp(0, 1)
            fid.update(sample_tensors, real=False)
            
            remaining_samples -= batch_size
        
        # Add validation images to FID
        for batch in val_dataloader:
            real_images = batch["images"].to(device)
            # Convert from [-1, 1] to [0, 1] range for FID
            real_images = (real_images * 0.5 + 0.5).clamp(0, 1)
            fid.update(real_images, real=True)
    
    # Calculate FID score
    fid_score = float(fid.compute())
    return fid_score


def calculate_fid_from_folders(
    real_dir: str,
    fake_dir: str,
    batch_size: int = 32,
    device: Optional[torch.device] = None,
    image_size: Optional[int] = None
) -> float:
    """Calculate FID score between images in two directories.
    
    Args:
        real_dir: Path to folder containing real images
        fake_dir: Path to folder containing generated/fake images
        batch_size: Batch size for processing images
        device: Device to run calculation on (defaults to cuda if available)
        image_size: Optional size to resize images to
        
    Returns:
        FID score
    """
    print(f"\nCalculating FID between:")
    print(f"Real images dir: {real_dir}")
    print(f"Fake images dir: {fake_dir}")
    
    if device is None:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    
    # Initialize FID
    fid = FrechetInceptionDistance(normalize=True).to(device)
    
    # Setup preprocessing
    transforms_list = [transforms.ToTensor()]
    if image_size is not None:
        transforms_list.insert(0, transforms.Resize((image_size, image_size)))
        print(f"Resizing images to {image_size}x{image_size}")
    preprocess = transforms.Compose(transforms_list)
    
    def process_folder(folder_path: str, is_real: bool):
        # Load dataset
        print(f"Processing {'real' if is_real else 'fake'} images from: {folder_path}")
        dataset = load_dataset("imagefolder", data_dir=folder_path, split="train")
        print(f"Found {len(dataset)} images")
        
        # Process images in batches
        n_processed = 0
        for i in range(0, len(dataset), batch_size):
            batch = dataset[i:i + batch_size]
            # Convert PIL images to tensors
            tensors = torch.stack([
                preprocess(img.convert("RGB")) for img in batch["image"]
            ]).to(device)
            fid.update(tensors, real=is_real)
            n_processed += len(batch["image"])
            
            if i % (batch_size * 10) == 0:  # Print progress every 10 batches
                print(f"Processed {n_processed}/{len(dataset)} images...")
        print(f"Finished processing {n_processed} images")
    
    # Process both folders
    process_folder(real_dir, True)
    process_folder(fake_dir, False)
    
    # Calculate and return FID score
    print("Calculating final FID score...")
    fid_score = float(fid.compute())
    print(f"FID Score: {fid_score:.4f}")
    return fid_score


def generate_and_calculate_fid_attributes(
    pipeline: DiffusionPipeline,
    val_dataloader: DataLoader,
    device: torch.device,
    preprocess,
    num_train_timesteps: int,
    num_samples: int,
    attributes: torch.Tensor,
) -> float:
    """Generate images with attribute conditioning and calculate FID score.
    
    Note: The pipeline generates images in [-1, 1] range, but FID calculation
    expects images in [0, 1] range. We handle this conversion here.
    
    Args:
        pipeline: The conditional diffusion pipeline
        val_dataloader: Validation dataloader
        device: Device to use for generation
        preprocess: Preprocessing transform
        num_train_timesteps: Number of timesteps for inference
        num_samples: Number of images to generate
        attributes: Tensor of shape (num_samples, num_attributes) containing
                   the attribute vectors to condition on
    
    Returns:
        FID score between generated and real images
    """
    # Move pipeline to device
    pipeline = pipeline.to(device)
    
    # Initialize FID metric
    fid = FrechetInceptionDistance(normalize=True).to(device)
    
    with torch.no_grad():
        # Generate images with attribute conditioning
        remaining_samples = num_samples
        sample_idx = 0
        
        while remaining_samples > 0:
            val_batch_size = val_dataloader.batch_size or 16
            batch_size = min(val_batch_size, remaining_samples)
            curr_attributes = attributes[sample_idx:sample_idx + batch_size]
            
            # Generate images
            generator = torch.Generator(device=device).manual_seed(sample_idx)
            output = pipeline(
                batch_size=batch_size,
                generator=generator,
                num_inference_steps=num_train_timesteps,
                class_labels=curr_attributes,
                output_type="pt"  # Return PyTorch tensors
            )
            sample_tensors = output.images.to(device)
            
            # Convert from [-1, 1] to [0, 1] range for FID
            sample_tensors = (sample_tensors * 0.5 + 0.5).clamp(0, 1)
            fid.update(sample_tensors, real=False)
            
            sample_idx += batch_size
            remaining_samples -= batch_size
        
        # Process validation images
        for batch in val_dataloader:
            if isinstance(batch, (tuple, list)):
                real_images = batch[0]  # Assuming images are the first element
            else:
                real_images = batch["images"]
            
            real_images = real_images.to(device)
            # Convert from [-1, 1] to [0, 1] range for FID
            real_images = (real_images * 0.5 + 0.5).clamp(0, 1)
            fid.update(real_images, real=True)
    
    # Calculate and return FID score
    fid_score = float(fid.compute())
    return fid_score
    
def generate_and_calculate_fid_attr_seg(
    config,
    model,
    pipeline,
    val_dataloader,
    val_attributes,
    vae=None,
    attribute_embedder=None,
    output_dir="outputs/val_images",
    num_samples=300,
):
    """Generates images conditioned on attributes + segmentation and calculates FID.

    Args:
        config: TrainingConfig
        model: UNet model
        pipeline: Custom diffusion pipeline (e.g., AttributeDiffusionPipeline)
        val_dataloader: Dataloader for val images
        val_attributes: [B, A] tensor of attribute conditioning
        vae: Optional VAE or VQModel
        attribute_embedder: Optional embedding module for attributes
        output_dir: Where to save generated images
        num_samples: Number of validation samples to generate for FID
    """
    os.makedirs(output_dir, exist_ok=True)
    model.eval()

    device = config.device
    total = 0
    image_list = []

    with torch.no_grad():
        for batch in tqdm(val_dataloader, desc="Generating images for FID"):
            if total >= num_samples:
                break

            # Unpack
            image, attributes, seg = batch
            bsz = image.size(0)
            image = image.to(device)
            attributes = attributes.to(device)
            seg = seg.to(device)

            if attribute_embedder:
                attr_embeds = attribute_embedder(attributes)  # [B, D]
            else:
                attr_embeds = attributes  # assume already embedded

            # Forward generation
            samples = pipeline(
                batch_size=bsz,
                condition_embeddings=attr_embeds,
                segmentation=seg,
                generator=torch.manual_seed(config.seed),
            ).images  # List of PILs or torch tensors depending on pipeline

            # Save generated images
            for i, sample in enumerate(samples):
                if isinstance(sample, torch.Tensor):
                    if sample.ndim == 3:
                        save_image(sample, os.path.join(output_dir, f"{total+i:05}.png"))
                else:
                    sample.save(os.path.join(output_dir, f"{total+i:05}.png"))

            total += bsz

    # FID calculation
    fid_score = fid.compute_fid(
        fdir1=output_dir,
        fdir2=config.val_dir,
        mode="clean",
        dataset_name=None
    )

    print(f"\n✅ FID (attribute + segmentation): {fid_score:.2f}")
    return fid_score