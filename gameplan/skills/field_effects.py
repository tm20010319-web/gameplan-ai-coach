"""Geometric corroboration for a large enemy area, not a cast classifier.

At the canonical HUD scale, a selected enemy's foot ring is small. A sustained
area spell surrounds the actor and has a much larger red enemy boundary. This
check deliberately leaves unsupported/occluded appearances unconfirmed.
"""
import cv2
import numpy as np

_angles=np.linspace(0,2*np.pi,120,endpoint=False)
_dx,_dy,_radius=np.meshgrid(np.arange(-30,31,15),np.arange(80,141,15),np.arange(120,211,10),indexing='ij')
_xs=(_dx[...,None]+_radius[...,None]*np.cos(_angles)).reshape(-1,120)
_ys=(_dy[...,None]+_radius[...,None]*.64*np.sin(_angles)).reshape(-1,120)


def large_enemy_field(picture,target):
    picture=picture.convert('RGB')
    picture=picture.resize((round(picture.width*576/picture.height),576))
    height,width=576,picture.width
    hsv=cv2.cvtColor(np.asarray(picture),cv2.COLOR_RGB2HSV)
    red=(((hsv[:,:,0]<12)|(hsv[:,:,0]>168))&(hsv[:,:,1]>100)&(hsv[:,:,2]>65)).astype(np.uint8)
    red=cv2.dilate(red,np.ones((5,5),np.uint8))
    xs=np.rint(target['x']*width+_xs).astype(int)
    ys=np.rint(target['y']*height+_ys).astype(int)
    visible=(xs>=0)&(xs<width)&(ys>=0)&(ys<height)
    hits=red[np.clip(ys,0,height-1),np.clip(xs,0,width-1)]*visible
    # Require a substantial arc distributed over the area, not a red health
    # bar, a projectile, or many red pixels in just one direction.
    quadrants=hits.reshape(-1,4,30).mean(axis=2)
    return bool(np.any((hits.mean(axis=1)>=.42)&((quadrants>=.2).sum(axis=1)>=3)))
