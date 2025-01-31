"""This dataloader is to generate the data for sdf as input.
----Zhangsihao Yang, Jan 8, 2021
"""
import json
import os

import numpy as np
import torch
from torch.utils.data import Dataset
from easydict import EasyDict


META_ROOT = os.path.join("../data/", "meta")
SDF_ROOT = os.path.join("../data/", "sdf")
sdf_fn = "sdf.npy"


def farthest_point_sample(point, npoint):
    """Farthest point sampling for numpy implementation.

    Input:
        xyz: point cloud data, [N, D]
        npoint: number of samples
    Return:
        centroids: sampled point cloud index, [npoint, D]
    """
    N, D = point.shape
    xyz = point[:, :3]
    centroids = np.zeros((npoint,))
    distance = np.ones((N,)) * 1e10
    farthest = np.random.randint(0, N)
    for i in range(npoint):
        centroids[i] = farthest
        centroid = xyz[farthest, :]
        dist = np.sum((xyz - centroid) ** 2, -1)
        mask = dist < distance
        distance[mask] = dist[mask]
        farthest = np.argmax(distance, -1)
    point = point[centroids.astype(np.int32)]
    return point


def get_sdf_path(name: [str]) -> str:
    """Get the path to the signed distance field file.

    Args:
        name: The list of string information.
            name[0]: The stage name.
            name[1]: The index name.

    Returns:
        The path to the sdf file.
    """
    assert len(name) == 2, "The length of the input name is not correct!"
    return os.path.join(SDF_ROOT, name[0], "{}.npz".format(name[1]))


def clean_name_list(name_list: list) -> list:
    """Clean redundant name from the name list.

    Args:
        name_list: The name list.

    Returns:
        return_name_list: The cleaned name list.
    """
    return_name_list = []
    for name in name_list:
        return_name = [name[0], name[1]]
        if return_name not in return_name_list:
            return_name_list.append(return_name)
    return return_name_list


class PointNetPlusPlus(Dataset):
    """Dataset for mesh classification for PointNet++.
    """
    def __init__(self, config, dataset: EasyDict, training: str):
        """Initialize the class.

        Args:
            config (module)
            dataset (easydict.EasyDict)
            training (str): the stage of training
        """
        self.config = config
        self.dataset = dataset
        self.training = training

        self.name_lists = {}

        # load meta list
        json_file_path = os.path.join(META_ROOT, self.dataset.meta_fn)
        with open(json_file_path, "r") as file:
            meta_list = json.load(file)

        # update the name list
        name_list = []
        for label in self.dataset.label:

            # the stage to be loaded
            if training == "train":
                identity_list = meta_list[label]["train"]
            elif training == "val":
                identity_list = meta_list[label]["val"]
            elif training == "test":
                identity_list = meta_list[label]["test"]
            else:
                raise ValueError("This stage {} is not known!".format(training))

            # concatenate stage label with identity and CL value
            stage_identity_list = [[label, identity[0], identity[1]] for identity in identity_list]
            name_list.extend(stage_identity_list)

        self.name_lists["name_list"] = name_list

        # load the scalar information
        with open(os.path.join(META_ROOT, "size.json"), "r") as file:
            self.radius_list = json.load(file)

    def __len__(self):
        return len(self.name_lists["name_list"])

    def __getitem__(self, index: int) -> dict:
        """The function to get an item in the dataset.

        Args:
            index:

        Returns:
            The dictionary to be returned.

        """
        # load the sdf
        stage_identity = self.name_lists["name_list"][index]
        sdf_file_name = os.path.join(
            SDF_ROOT, stage_identity[0], stage_identity[1], sdf_fn
        )
        sdf = np.load(sdf_file_name)

        # sample the sdf
        idx = np.random.randint(sdf.shape[0], size=self.dataset.sdf_sample_number)
        sdf = sdf[idx, :]

        # scale the sdf
        radius = self.radius_list[stage_identity[0]][stage_identity[1]]
        sdf = sdf * float(radius) / self.dataset.scalar

        # revert the sdf.
        revert = False
        if revert:
            last_sdf = sdf[:, 3]
            last_sdf = 1 - np.abs(last_sdf)
            sdf[:, 3] = last_sdf

        # label
        label = stage_identity[2]

        return {
            "label": label, "point_sdf": sdf,
        }

    @staticmethod
    def collate(batch: [dict]) -> dict:
        """Collate batch together for training.

        Args:
            batch: A list of dict. In each dictionary, There are
                "label": The label of this item.
                "point": The point of this item.

        Returns:
            The dictionary of collated batch.
                "point": Tensor with shape (B, N, 3).
                "label": tensor with shape (B) contains label.
        """
        # Point.
        point = torch.stack([torch.from_numpy(item["point_sdf"]) for item in batch])

        # label
        label = torch.cat([torch.tensor(item["label"]).view(1) for item in batch], dim=0)

        return {
            "label": label,
            "xyz_sdf": point,
            "error_image": label.reshape(1, label.size(dim=0)).transpose(0, 1)
        }


def visualize_point(points: np.ndarray, scene):
    """Visualize the signed distance field.
    Args:
        points: The locations. (N, 3)
        scene: The scene to render the point cloud.
    Returns:
        scene: The scene to render the point cloud.
    """
    import pyrender

    cloud = pyrender.Mesh.from_points(points)

    scene.add(cloud)

    return scene


def plot_sdf(point_sdf, scene):

    import pyrender

    points = point_sdf[:, :3]
    sdf = point_sdf[:, 3]

    colors = np.zeros(points.shape)
    colors[sdf < 0, 2] = 1
    colors[sdf > 0, 0] = 1

    cloud = pyrender.Mesh.from_points(points, colors=colors)
    scene.add(cloud)

    return scene


def rotate_point_sdf(point_sdf):
    point = point_sdf[:, :3]
    from scipy.spatial.transform import Rotation as R
    r1 = R.from_euler('x', 145, degrees=True).as_matrix()
    point = point @ r1
    point_sdf[:, :3] = point
    return point_sdf


def convert_sdf_to_meshgrid(point_sdf, depth, threshold):
    resolution = 256
    x = point_sdf[:, 0]
    y = point_sdf[:, 1]
    z = point_sdf[:, 2]
    sdf = point_sdf[:, 3]
    filter_depth = np.abs(z - depth) < threshold

    x = x[filter_depth]
    y = y[filter_depth]
    sdf = sdf[filter_depth]
    # sdf[sdf < 0.0] = sdf[sdf < 0.0] / sdf.min() * sdf.max() * -1.0
    sdf[sdf < 0.0] = -1.0
    sdf[sdf > 0.0] = 1.0

    grid_y, grid_x = np.meshgrid(np.linspace(-1, 1, resolution), np.linspace(-1, 1, resolution))

    x_index = np.floor((x + 1.0) / 2 * resolution).astype(np.int)
    y_index = np.floor((y + 1.0) / 2 * resolution).astype(np.int)

    z = grid_x * 0.0
    z[x_index, y_index] = sdf

    return grid_x, grid_y, z


if __name__ == '__main__':
    pass
