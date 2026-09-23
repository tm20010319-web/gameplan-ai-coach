"""Independent displacement check: camera panning is not a hero blink."""
import cv2
import numpy as np


def displaced(previous,current,before,after):
    if previous.shape!=current.shape:return False
    height,width=previous.shape
    mask=np.zeros_like(previous)
    mask[round(height*.13):round(height*.78),round(width*.2):round(width*.76)]=255
    for target in (before,after):
        cv2.circle(mask,(round(target['x']*width),round(target['y']*height+50)),85,0,-1)
    detector=cv2.ORB_create(nfeatures=500)
    kp1,des1=detector.detectAndCompute(previous,mask);kp2,des2=detector.detectAndCompute(current,mask)
    if des1 is None or des2 is None:return False
    pairs=cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(des1,des2,k=2)
    good=[a for pair in pairs if len(pair)==2 for a,b in [pair] if a.distance<.7*b.distance]
    if len(good)<15:return False
    p1=np.float32([kp1[m.queryIdx].pt for m in good]);p2=np.float32([kp2[m.trainIdx].pt for m in good])
    matrix,inliers=cv2.estimateAffinePartial2D(p1,p2,method=cv2.RANSAC,ransacReprojThreshold=2)
    if matrix is None or inliers.mean()<.6:return False
    expected=matrix@np.array([before['x']*width,before['y']*height,1])
    actual=np.array([after['x']*width,after['y']*height])
    return bool(np.linalg.norm(actual-expected)>width*.025)
