
---

# Expandex

Expandex is a Python library designed to find images similar to a given source image. It utilizes online image searches, feature extraction, and image deduplication to retrieve unique and usable images.

## Installation

You can install Expandex via pip:

```bash
pip install expandex
```

## Usage

```python
from expandex import Locator

# Initialize Locator object
locator = Locator()

# Scout for similar images
similar_images = locator.scout('path_to_source_image.jpg')

# Display similar images
for image_url in similar_images:
    print(image_url)
```

## Features

- **Feature Extraction:** Employs feature extraction techniques to analyze and compare image features.
- **Image Deduplication:** Prevents the retrieval of duplicate images to ensure diversity in search results.
- **Multi-threaded Processing:** Utilizes multi-threading to enhance performance and speed up the image retrieval process.

## Configuration

You can configure Expandex by specifying parameters such as save folder location, deduplication method, and weights for similarity metrics.

```python
# Example Configuration
locator = Locator(
    save_folder='images',
    deduplicate='cpu',
    weights={
        'ih': 0.1,  # Image Hash similarity
        'ssim': 0.15,  # Structural similarity index measurement
        'cs': 0.1,  # NumPy cosine similarity
        'cnn': 0.15,  # EfficientNet feature extraction
        'dedup': 0.1  # Mobilenet cosine similarities
    },
    debug=True
)
```

## License

Expandex is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for more details.

---