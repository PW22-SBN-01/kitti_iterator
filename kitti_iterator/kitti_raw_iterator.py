import os
import yaml
import numpy as np
import scipy

from torch.utils.data import Dataset
from torch.multiprocessing import Process, Queue, set_start_method
import torch

import cv2
import itertools
import glob

from .helper import *

# Sensor Setup: https://www.cvlibs.net/datasets/kitti/setup.php

plot3d = False
plot2d = False
point_cloud_array = None
if __name__ == '__main__':
    if plot3d:
        set_start_method('spawn')
        point_cloud_array = Queue()

def open_yaml(settings_doc):
    settings_doc = settings_doc
    cam_settings = {}
    with open(settings_doc, 'r') as stream:
        try:
            cam_settings = yaml.load(stream, Loader=yaml.FullLoader)
        except yaml.YAMLError as exc:
            print(exc)
    return cam_settings

def open_calib(calib_file):
    data = open_yaml(calib_file)
    for k in data:
        try:
            data[k] = np.array(list(map(float, data[k].split(" "))))
        except:
            pass
    return data

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

def gaus_blur_3D(data, sigma = 1.0, n=5, device = device):
    # first build the smoothing kernel
    x = np.arange(-n,n+1,1)   # coordinate arrays -- make sure they contain 0!
    y = np.arange(-n,n+1,1)
    z = np.arange(-n,n+1,1)
    xx, yy, zz = np.meshgrid(x,y,z)
    kernel = np.exp(-(xx**2 + yy**2 + zz**2)/(2*sigma**2))

    with torch.no_grad():
        kernel = torch.tensor(kernel).unsqueeze(0).unsqueeze(0).to(device=device, dtype=torch.float32)
        data = torch.tensor(data).unsqueeze(0).to(device=device, dtype=torch.float32)

        filtered = torch.nn.functional.conv3d(data, kernel, stride=1, padding=n)

        # return filtered.cpu().detach().squeeze().numpy()
        return filtered.cpu().detach().numpy()

def gaus_blur_3D_cpu(data, sigma = 1.0, n=5):
    # first build the smoothing kernel
    x = np.arange(-n,n+1,1)   # coordinate arrays -- make sure they contain 0!
    y = np.arange(-n,n+1,1)
    z = np.arange(-n,n+1,1)
    xx, yy, zz = np.meshgrid(x,y,z)
    kernel = np.exp(-(xx**2 + yy**2 + zz**2)/(2*sigma**2))

    # filtered = signal.convolve(data, kernel, mode="same")
    # filtered = np.convolve(data, kernel, 'same')
    filtered = scipy.ndimage.convolve(data, kernel)
    
    return filtered


class KittiRaw(Dataset):

    def __init__(self, 
        kitti_raw_base_path="kitti_raw_mini",
        date_folder="2011_09_26",
        sub_folder="2011_09_26_drive_0001_sync",
        transform=dict(),
        grid_size = (200.0, 200.0, 10.0),
        scale = 4.0,
        sigma = None,
        gaus_n = 4
    ) -> None:
        self.gaus_n = gaus_n
        self.sigma = sigma
        self.transform = transform
        self.plot3d = True
        self.plot2d = True
        self.scale = scale
        self.grid_size = grid_size
        self.occupancy_shape = list(map(lambda i: int(i*self.scale), self.grid_size))
        self.occupancy_mask_2d_shape = list(map(lambda i: int(i*self.scale), self.grid_size[:2]))
        self.grid_x, self.grid_y, self.grid_z = list(map(lambda i: i//2, self.grid_size))
        self.occ_x, self.occ_y, self.occ_z = self.occupancy_shape

        self.kitti_raw_path = os.path.join(kitti_raw_base_path, date_folder)
        self.raw_data_path = os.path.join(self.kitti_raw_path, sub_folder)
        self.image_00_path = os.path.join(self.raw_data_path, "image_00")
        self.image_01_path = os.path.join(self.raw_data_path, "image_01")
        self.image_02_path = os.path.join(self.raw_data_path, "image_02")
        self.image_03_path = os.path.join(self.raw_data_path, "image_03")
        self.oxts_path = os.path.join(self.raw_data_path, "oxts")
        self.velodyne_points_path = os.path.join(self.raw_data_path, "velodyne_points")
        self.calib_cam_to_cam_txt = os.path.join(self.kitti_raw_path, "calib_cam_to_cam.txt")
        self.calib_imu_to_velo_txt = os.path.join(self.kitti_raw_path, "calib_imu_to_velo.txt")
        self.calib_velo_to_cam_txt = os.path.join(self.kitti_raw_path, "calib_velo_to_cam.txt")

        self.calib_cam_to_cam = open_calib(self.calib_cam_to_cam_txt)
        self.calib_imu_to_velo = open_calib(self.calib_imu_to_velo_txt)
        self.calib_velo_to_cam = open_calib(self.calib_velo_to_cam_txt)

        self.R = np.reshape(self.calib_velo_to_cam['R'], (3,3))
        self.T = np.reshape(self.calib_velo_to_cam['T'], (3,1))

        self.K_00 = np.reshape(self.calib_cam_to_cam['K_00'], (3,3))
        self.S_00 = np.reshape(self.calib_cam_to_cam['S_00'], (1,2))
        self.D_00 = np.reshape(self.calib_cam_to_cam['D_00'], (1,5))
        self.R_00 = np.reshape(self.calib_cam_to_cam['R_00'], (3,3))
        self.T_00 = np.reshape(self.calib_cam_to_cam['T_00'], (3,1))

        self.K_01 = np.reshape(self.calib_cam_to_cam['K_01'], (3,3))
        self.S_01 = np.reshape(self.calib_cam_to_cam['S_01'], (1,2))
        self.D_01 = np.reshape(self.calib_cam_to_cam['D_01'], (1,5))
        self.R_01 = np.reshape(self.calib_cam_to_cam['R_01'], (3,3))
        self.T_01 = np.reshape(self.calib_cam_to_cam['T_01'], (3,1))

        self.K_02 = np.reshape(self.calib_cam_to_cam['K_02'], (3,3))
        self.S_02 = np.reshape(self.calib_cam_to_cam['S_02'], (1,2))
        self.D_02 = np.reshape(self.calib_cam_to_cam['D_02'], (1,5))
        self.R_02 = np.reshape(self.calib_cam_to_cam['R_02'], (3,3))
        self.T_02 = np.reshape(self.calib_cam_to_cam['T_02'], (3,1))

        self.K_03 = np.reshape(self.calib_cam_to_cam['K_03'], (3,3))
        self.S_03 = np.reshape(self.calib_cam_to_cam['S_03'], (1,2))
        self.D_03 = np.reshape(self.calib_cam_to_cam['D_03'], (1,5))
        self.R_03 = np.reshape(self.calib_cam_to_cam['R_03'], (3,3))
        self.T_03 = np.reshape(self.calib_cam_to_cam['T_03'], (3,1))

        self.w, self.h = list(map(int, (self.S_00[0][0], self.S_00[0][1])))
        self.new_K_00, self.roi_00 = cv2.getOptimalNewCameraMatrix(self.K_00, self.D_00, (self.w, self.h), 1, (self.w, self.h))
        self.x_00, self.y_00, self.w_00, self.h_00 = self.roi_00

        self.new_K_01, self.roi_01 = cv2.getOptimalNewCameraMatrix(self.K_01, self.D_01, (self.w, self.h), 1, (self.w, self.h))
        self.x_01, self.y_01, self.w_01, self.h_01 = self.roi_01

        self.new_K_02, self.roi_02 = cv2.getOptimalNewCameraMatrix(self.K_02, self.D_02, (self.w, self.h), 1, (self.w, self.h))
        self.x_02, self.y_02, self.w_02, self.h_02 = self.roi_02

        self.new_K_03, self.roi_03 = cv2.getOptimalNewCameraMatrix(self.K_03, self.D_03, (self.w, self.h), 1, (self.w, self.h))
        self.x_03, self.y_03, self.w_03, self.h_03 = self.roi_03

        self.intrinsic_mat = self.new_K_02
        self.intrinsic_mat = np.vstack((
            np.hstack((
                self.intrinsic_mat, np.zeros((3,1))
            )), 
            np.zeros((1,4))
        ))

        self.img_list = sorted(os.listdir(os.path.join(self.image_00_path, 'data')))
        self.img_list = list(map(lambda x: x.split(".png")[0], self.img_list))
        self.index = 0

    def __len__(self):
        return len(self.img_list)

    def __iter__(self):
        self.index = 0
        return self
    
    def __next__(self):
        if self.index>=self.__len__():
            raise StopIteration
        data = self[self.index]
        self.index += 1
        return data

    def transform_occupancy_grid_to_points_serial(self, occupancy_grid, threshold=0.5):
        occupancy_grid = occupancy_grid.squeeze()
        final_points = set()
        for i in range(occupancy_grid.shape[0]):
            for j in range(occupancy_grid.shape[1]):
                for k in range(occupancy_grid.shape[2]):
                    x,y,z = [
                        # (i - occ_x/2) * grid_x / (occ_x/2),
                        (i) * self.grid_x / (self.occ_x/2),
                        (j - self.occ_y/2) * self.grid_y / (self.occ_y/2),
                        (k - self.occ_z/2) * self.grid_z / (self.occ_z/2)
                    ]
                    if occupancy_grid[i,j,k] > threshold:
                        final_points.add((x,y,z))
                    else:
                        final_points.add((0,0,0))
                        # if (x,y,z) not in final_points:
                        #     final_points.add((x,y,z))
        final_points = list(final_points)
        final_points = np.array(final_points, dtype=np.float32)
        return final_points
    
    def transform_occupancy_grid_to_points(self, occupancy_grid, threshold=0.5, device=device, skip=3):
        occupancy_grid = occupancy_grid.squeeze()
        # occupancy_grid = torch.tensor(occupancy_grid, device=device)
        def f(xi):
            i, j, k = xi
            x,y,z = [
                (i) * self.grid_x / (self.occ_x/2),
                (j - self.occ_y/2) * self.grid_y / (self.occ_y/2),
                (k - self.occ_z/2) * self.grid_z / (self.occ_z/2)
            ]
            if occupancy_grid[i,j,k] > threshold:
                return (x,y,z)
            return (0,0,0)

        # np.array([f(xi) for xi in x])
        final_points = np.array([f(xi) for xi in itertools.product(
            range(0, occupancy_grid.shape[0], skip),
            range(0, occupancy_grid.shape[1], skip),
            range(0, occupancy_grid.shape[2], skip)
        )])
        # final_points = np.fromfunction(lambda xi: f(xi), np.indices(occupancy_grid.shape))

        # final_points = final_points[np.logical_not(
        #     np.logical_and(final_points[:,0] == 0, final_points[:,1] == 0, final_points[:,2] == 0,)
        # )]

        # final_points = final_points.cpu().detach().numpy()
        final_points = np.array(final_points, dtype=np.float32)
        return final_points

    def transform_points_to_image_space(self, velodyine_points, roi, intrinsic_mat, R_cam, T_cam, P_rect):
        x, y, w, h = roi
        intrinsic_mat = intrinsic_mat
        intrinsic_mat = np.vstack((
            np.hstack((
                intrinsic_mat, np.zeros((3,1))
            )), 
            np.zeros((1,4))
        ))

        # image_points = cv2.resize(image_points, (w, h))
        image_points = np.zeros((h,w,3))
        
        ans, color = velo3d_2_camera2d_points(velodyine_points, self.R, self.T, P_rect, v_fov=(-24.9, 2.0), h_fov=(-45,45))
        
        for index in range(len(ans[0])):
            img_x, img_y = [ans[0][index], ans[1][index]]
            if (0 <= img_x < w and 0 <= img_y < h):
                image_points[int(img_y),int(img_x),:] = color[index]
        
        return image_points

    def transform_occupancy_grid_to_image_space(self, occupancuy_grid, roi, intrinsic_mat, R_cam, T_cam, P_rect):
        pc = self.transform_occupancy_grid_to_points(occupancuy_grid, threshold=0.0, skip=1)
        image_points = self.transform_points_to_image_space(pc, roi, intrinsic_mat, R_cam, T_cam, P_rect)
        return image_points
        

    def transform_points_to_occupancy_grid(self, velodyine_points):
        occupancy_grid = np.zeros(self.occupancy_shape, dtype=np.float32)
        occupancy_mask_2d = np.zeros(self.occupancy_mask_2d_shape, dtype=np.uint8)
        x, y, w, h = self.roi_02

        P_rect = self.calib_cam_to_cam['P_rect_02'].reshape(3, 4)[:3,:3]

        # ans, color = velo3d_2_camera2d_points(velodyine_points, self.R, self.T, P_rect, v_fov=(-24.9, 2.0), h_fov=(-45,45))
        v_fov=(-24.9, 2.0)
        h_fov=(-45,45)
        velodyine_points, c_ = velo_points_filter(velodyine_points, v_fov, h_fov)
        velodyine_points_orig = velodyine_points.copy()

        velodyine_points_camera = []

        RT_ = np.concatenate((self.R, self.T),axis = 1)
    
        # convert velodyne coordinates(X_v, Y_v, Z_v) to camera coordinates(X_c, Y_c, Z_c) 
        for i in range(velodyine_points.shape[1]):
            velodyine_points[:3,i] = np.matmul(RT_, velodyine_points[:,i])

        velodyine_points = np.delete(velodyine_points, 3, axis=0)
        image_points = velodyine_points.copy()
        # print(image_points.shape, velodyine_points.shape)
        # convert camera coordinates(X_c, Y_c, Z_c) image(pixel) coordinates(x,y) 
        for i in range(image_points.shape[1]):
            image_points[:,i] = np.matmul(P_rect, image_points[:,i])

        image_points = image_points[::]/image_points[::][2]
        image_points = np.delete(image_points, 2, axis=0)
        # print(image_points.shape, velodyine_points.shape)

        for index in range(image_points.shape[1]):
            img_x, img_y = image_points[:, index]
            x, y, z = velodyine_points[:, index]
    
            # x, y, z = x, z, y # Half
            # x, y, z = y, x, z # N
            # x, y, z = y, z, x # N
            x, y, z = z, x, -y # Inverted
            # x, y, z = z, y, x

            xv, yv, zv = velodyine_points_orig[:3, index]
            i, j, k = [
                # int((p[0]*self.occ_x//2)//self.grid_x + self.occ_x//2),
                int((x*self.occ_x//2)//self.grid_x)*2,
                int((y*self.occ_y//2)//self.grid_y + self.occ_y//2),
                # int((p[1]*self.occ_y//2)//self.grid_y),
                int((z*self.occ_z//2)//self.grid_z + self.occ_z//2)
            ]

            # if (
            #     0 < i < self.occupancy_shape[0] and
            #     0 < j < self.occupancy_shape[1] and
            #     0 < k < self.occupancy_shape[2]
            # ):
            if (
                (0 <= img_x < w and 0 <= img_y < h) and
                0 < i < self.occupancy_shape[0] and
                0 < j < self.occupancy_shape[1] and
                0 < k < self.occupancy_shape[2]
                # 0 < xv < self.grid_x and 
                # -self.grid_y < yv < self.grid_y and 
                # -self.grid_z < zv < self.grid_z
            ):
                velodyine_points_camera.append((x,y,z))
                occupancy_grid[i,j,k] = 1.0
                # occupancy_mask_2d[i,j] = int(min(255, 255*max(0, (k-6)/(15-6))))
                occupancy_mask_2d[i,j] = max(int(min(255, 255*max(0, (k-6)/(15-6)))), occupancy_mask_2d[i,j])

        
        # velodyine_points_camera = []
        # for index in range(velodyine_points.shape[1]):
        #     velodyine_points_camera.append(velodyine_points[:,index])
        
        velodyine_points_camera = np.array(velodyine_points_camera, dtype=np.float32)

        return {
            'occupancy_grid': occupancy_grid, 
            'occupancy_mask_2d': occupancy_mask_2d,
            'velodyine_points_camera': velodyine_points_camera
        }
    
    def transform_points_to_occupancy_grid_OLD(self, velodyine_points):
        occupancy_grid = np.zeros(self.occupancy_shape, dtype=np.float32)
        occupancy_mask_2d = np.zeros(self.occupancy_mask_2d_shape, dtype=np.uint8)
        x, y, w, h = self.roi_02

        min_height = float('inf')
        max_height = -float('inf')
        for p in velodyine_points:
            p3d = np.array([
                p[0], p[1], p[2]
            ]).reshape((3,1))
            p3d = p3d - self.T
            p3d = self.R @ p3d
            p4d = np.ones((4,1))
            p4d[:3,:] = p3d
            p2d = self.intrinsic_mat @ p4d
            if p2d[2][0]!=0:
                img_x, img_y = p2d[0][0]//p2d[2][0], p2d[1][0]//p2d[2][0]
                if (0 <= img_x < w and 0 <= img_y < h and p3d[2]>0) and 0<p[0]<self.grid_x and -self.grid_y<p[1]<self.grid_y and -self.grid_z<p[2]<self.grid_z:
                    i, j, k = [
                        # int((p[0]*self.occ_x//2)//self.grid_x + self.occ_x//2),
                        int((p[0]*self.occ_x//2)//self.grid_x)*2,
                        int((p[1]*self.occ_y//2)//self.grid_y + self.occ_y//2),
                        # int((p[1]*self.occ_y//2)//self.grid_y),
                        int((p[2]*self.occ_z//2)//self.grid_z + self.occ_z//2)
                    ]
                    occupancy_grid[i,j,k] = 1.0
                    # occupancy_mask_2d[i,j] = int(min(255, 255*max(0, (k-6)/(15-6))))
                    occupancy_mask_2d[i,j] = max(int(min(255, 255*max(0, (k-6)/(15-6)))), occupancy_mask_2d[i,j])

                    min_height = min(min_height, k)
                    max_height = max(max_height, k)
        
        if type(self.sigma)==float:
            occupancy_grid = gaus_blur_3D(occupancy_grid, sigma=self.sigma, n=self.gaus_n)
            # occupancy_grid = torch.nn.Sigmoid()(occupancy_grid)

        occupancy_mask_2d = cv2.flip(occupancy_mask_2d, 0)
        return {
            'occupancy_grid': occupancy_grid, 
            'occupancy_mask_2d': occupancy_mask_2d
        }


    def __getitem__(self, index):
        id = self.img_list[index]
        image_00 = os.path.join(self.image_00_path, 'data', id + ".png")
        image_01 = os.path.join(self.image_01_path, 'data', id + ".png")
        image_02 = os.path.join(self.image_02_path, 'data', id + ".png")
        image_03 = os.path.join(self.image_03_path, 'data', id + ".png")
        velodyine_points = os.path.join(self.velodyne_points_path, 'data', id + ".bin")
        
        assert os.path.exists(image_00)
        assert os.path.exists(image_01)
        assert os.path.exists(image_02)
        assert os.path.exists(image_03)
        assert os.path.exists(velodyine_points)

        image_00_raw = cv2.imread(image_00)
        image_01_raw = cv2.imread(image_01)
        image_02_raw = cv2.imread(image_02)
        image_03_raw = cv2.imread(image_03)
        
        x, y, w, h = self.roi_00
        image_00 = cv2.undistort(image_00_raw, self.K_00, self.D_00, None, self.new_K_00)
        image_00 = image_00[y:y+h, x:x+w]

        x, y, w, h = self.roi_01
        image_01 = cv2.undistort(image_01_raw, self.K_01, self.D_01, None, self.new_K_01)
        image_01 = image_01[y:y+h, x:x+w]

        x, y, w, h = self.roi_02
        image_02 = cv2.undistort(image_02_raw, self.K_02, self.D_02, None, self.new_K_02)
        image_02 = image_02[y:y+h, x:x+w]

        x, y, w, h = self.roi_03
        image_03 = cv2.undistort(image_03_raw, self.K_03, self.D_03, None, self.new_K_03)
        image_03 = image_03[y:y+h, x:x+w]


        # velodyine_points = np.fromfile(velodyine_points, dtype=np.float32)
        # velodyine_points = np.reshape(velodyine_points, (velodyine_points.shape[0]//4, 4))
        velodyine_points = np.fromfile(velodyine_points, dtype=np.float32).reshape(-1, 4)[:,:3]
        
        occupancy_grid_data = self.transform_points_to_occupancy_grid(velodyine_points)

        data = {
            'image_00': image_00, 
            'image_01': image_01, 
            'image_02': image_02, 
            'image_03': image_03,
            'image_00_raw': image_00_raw, 
            'image_01_raw': image_01_raw, 
            'image_02_raw': image_02_raw, 
            'image_03_raw': image_03_raw,
            'roi_00': self.roi_00,
            'roi_01': self.roi_01,
            'roi_02': self.roi_02,
            'roi_03': self.roi_03,
            'K_00': self.K_00,
            'K_01': self.K_01,
            'K_02': self.K_02,
            'K_03': self.K_03,
            
            'R_00': self.R_00,
            'R_01': self.R_01,
            'R_02': self.R_02,
            'R_03': self.R_03,

            'T_00': self.T_00,
            'T_01': self.T_01,
            'T_02': self.T_02,
            'T_03': self.T_03,

            'calib_cam_to_cam': self.calib_cam_to_cam,
            'calib_imu_to_velo': self.calib_imu_to_velo,
            'calib_velo_to_cam': self.calib_velo_to_cam,

            'velodyine_points': velodyine_points, 
            'occupancy_grid': occupancy_grid_data['occupancy_grid'],
            'occupancy_mask_2d': occupancy_grid_data['occupancy_mask_2d'],
            'velodyine_points_camera': occupancy_grid_data['velodyine_points_camera']
        }
        for key in self.transform:
            data[key] = self.transform[key](data[key])
        return data

def get_kitti_tree(kitti_raw_base_path):
    date_folder_list = list(filter(os.path.isdir, glob.glob(os.path.join(kitti_raw_base_path, '*'))))
    date_folder_list = list(filter(lambda i: len(i.split('_'))==3, date_folder_list))
    kitti_tree = dict()
    for date_folder in date_folder_list:
        date_id = date_folder.split('/')[-1]
        # print(date_id)
        sub_folder_list = list(filter(os.path.isdir, glob.glob(os.path.join(date_folder, '*'))))
        sub_folder_list = list(filter(lambda i: len(i.split('/')[-1].split('_'))==6, sub_folder_list))
        sub_folder_list = list(map(lambda i: i.split('/')[-1], sub_folder_list))

        kitti_tree[date_id] = sub_folder_list
        # print(sub_folder_list)
    return kitti_tree

def get_kitti_raw(**kwargs):
    kitti_raw_base_path=kwargs['kitti_raw_base_path']
    kitti_tree = get_kitti_tree(kitti_raw_base_path)
    kitti_raw = []
    for date_folder in kitti_tree:
        for sub_folder in kitti_tree[date_folder]:
            kitti_raw.append(
                KittiRaw(
                    # kitti_raw_base_path=kitti_raw_base_path,
                    date_folder=date_folder,
                    sub_folder=sub_folder,
                    **kwargs
                )
            )
    return kitti_raw

def main(point_cloud_array=point_cloud_array):
    # k_raw = KittiRaw()
    # k_raw = KittiRaw(
    #     # kitti_raw_base_path="kitti_raw_mini",
    #     # date_folder="2011_09_26",
    #     # sub_folder="2011_09_26_drive_0001_sync",
    #     grid_size = (100.0, 50.0, 5),
    #     scale = 2.24 * 2,
    #     sigma = 5.0,
    #     # sigma = None,
    #     gaus_n=5
    # )

    # k_raw = KittiRaw(
    #     # kitti_raw_base_path="/home/aditya/Datasets/kitti/raw/",
    #     kitti_raw_base_path=os.path.expanduser("~/Datasets/kitti/raw/"),
    #     # date_folder="2011_09_26",
    #     # sub_folder="2011_09_26_drive_0001_sync",
    #     grid_size = (200.0, 50.0, 10),
    #     scale = 1.5,
    #     sigma = 1.0,
    #     # sigma = None,
    #     gaus_n=1
    # )
    kitti_raw_base_path=os.path.expanduser("~/Datasets/kitti/raw/")
    kitti_raw = get_kitti_raw(kitti_raw_base_path=kitti_raw_base_path)
    print('len(kitti_raw)', len(kitti_raw))
    return

    k_raw = KittiRaw(
        kitti_raw_base_path=os.path.expanduser("~/Datasets/kitti/raw/"),
        grid_size = (150.0, 74.0, 17.0),
        scale = 1.0,
        sigma = 1.0,
        gaus_n=1
    )

    print("Found", len(k_raw), "images ")
    for index in range(len(k_raw)):
        data = k_raw[index]
        image_02 = data['image_02']
        velodyine_points = data['velodyine_points']
        velodyine_points_camera = data['velodyine_points_camera']
        occupancy_mask_2d = data['occupancy_mask_2d']
        occupancy_grid  = data['occupancy_grid']
        img_id = '_00'
        roi = data['roi'+img_id]
        R_cam = data['R'+img_id]
        T_cam = data['T'+img_id]

        calib_cam_to_cam = data['calib_cam_to_cam']
        calib_imu_to_velo = data['calib_imu_to_velo']
        calib_velo_to_cam = data['calib_velo_to_cam']

        P_rect = calib_cam_to_cam['P_rect' + img_id].reshape(3, 4)[:3,:3]

        
        x, y, w, h = roi
        # img_input = data['image'+img_id+'_raw']
        img_input = data['image'+img_id]
        img_input = cv2.resize(img_input, (w, h))
        
        # image_points = k_raw.transform_points_to_image_space(velodyine_points, roi, data['K'+img_id], R_cam, T_cam, P_rect)
        image_points = k_raw.transform_occupancy_grid_to_image_space(occupancy_grid, roi, data['K'+img_id], R_cam, T_cam, P_rect)
        
        image_points = cv2.normalize(image_points - np.min(image_points.flatten()), None, 0, 255, norm_type=cv2.NORM_MINMAX, dtype=cv2.CV_8U)
        # image_points = cv2.addWeighted(image_points, 0.5, img_input, 0.1, 0.0)

        dilatation_size = 3
        dilation_shape = cv2.MORPH_ELLIPSE
        element = cv2.getStructuringElement(dilation_shape, (2 * dilatation_size + 1, 2 * dilatation_size + 1),
                                        (dilatation_size, dilatation_size))
        image_points = cv2.dilate(image_points, element)

        if plot2d:
            cv2.imshow('img_input', img_input)
            # cv2.imshow('image_points', cv2.normalize(image_points - np.min(image_points.flatten()), None, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX, dtype=cv2.CV_8UC1))
            # cv2.imshow('image_points', cv2.normalize(image_points, None, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX, dtype=cv2.CV_8UC1))
            # cv2.imshow('image_points', cv2.normalize(image_points - np.min(image_points.flatten()), None, 255, 0, norm_type=cv2.NORM_MINMAX, dtype=cv2.CV_8U))
            # cv2.imshow('image_points', cv2.normalize(image_points - np.min(image_points.flatten()), None, 0, 255, norm_type=cv2.NORM_MINMAX, dtype=cv2.CV_8U))
            # image_points_grid = k_raw.transform_occupancy_grid_to_image_space(occupancy_grid, roi, data['K'+img_id], R_cam, T_cam, P_rect)
            cv2.imshow('image_points', image_points - np.min(image_points.flatten()))
            # cv2.imshow('image_points_grid', image_points_grid - np.min(image_points_grid.flatten()))

            # cv2.imshow('occupancy_mask_2d', occupancy_mask_2d)
            key = cv2.waitKey(100)
            if key == ord('q'):
                return

        if plot3d:
            # print("Before transform_occupancy_grid_to_points")
            
            final_points = k_raw.transform_occupancy_grid_to_points(occupancy_grid, skip=1)
            # final_points = velodyine_points_camera
            # final_points = velodyine_points
            
            # print("k_raw.occupancy_shape", k_raw.occupancy_shape)
            # print("occupancy_grid.shape", occupancy_grid.shape)
            print("final_points.shape", final_points.shape)
            print(np.sum(occupancy_grid))

            MESHES = {
                'vertexes': np.array([]),
                'faces': np.array([]), 
                'faceColors': np.array([])
            }
            point_cloud_array.put({
                'POINTS': final_points,
                'MESHES': MESHES
            })

if __name__ == "__main__":
    if plot3d:
        image_loop_proc = Process(target=main, args=(point_cloud_array, ))
        image_loop_proc.start()
        
        from . import plotter
        plotter.start_graph(point_cloud_array)

        image_loop_proc.join()
    else:
        main(None)
