#!/usr/bin/env python

# ---------------------------------------------------------------------------
# Licensing Information: You are free to use or extend these projects for
# education or reserach purposes provided that (1) you retain this notice
# and (2) you provide clear attribution to UC Berkeley, including a link
# to http://barc-project.com
#
# Attibution Information: The barc project ROS code-base was developed
# at UC Berkeley in the Model Predictive Control (MPC) lab by Jon Gonzales
# (jon.gonzales@berkeley.edu). The cloud services integation with ROS was developed
# by Kiet Lam  (kiet.lam@berkeley.edu). The web-server app Dator was
# based on an open source project by Bruce Wootton

# This estimator was developed by Shuqi Xu (shuqixu@berkeley.edu)
# ---------------------------------------------------------------------------

import rospy
from Localization_helpers import Track
from barc.msg import ECU, pos_info, Vel_est
from sensor_msgs.msg import Imu
from marvelmind_nav.msg import hedge_imu_fusion
from std_msgs.msg import Header
from numpy import eye, zeros, diag, unwrap, tan, cos, sin, vstack, linalg
from numpy import ones, polyval, size, dot
from scipy.linalg import inv
from tf import transformations
import math
import numpy as np
import os


def main():
    # node initialization
    rospy.init_node("state_estimation")
    a_delay     = 0.0
    df_delay    = 0.2
    loop_rate   = 50
    dt = 1/loop_rate
    qa = 1000
    qp = 1000
    
    # # For experiment
    #                    # x,            y,            vx,          vy,       ax,       ay,      psi,    psidot
    # Q = 0.1*diag([1./20*dt**5*qa,1./20*dt**5*qa,1./3*dt**3*qa,1./3*dt**3*qa,   dt*qa,   dt*qa, 1./3*dt**3*qp,  dt*qp])
    #               # x_meas, y_meas,  vel_meas,  psiDot_meas, a_x_meas,  a_y_meas  vy_meas   
    # R = 0.1*diag([100.0,    100.0,     1.0,         1.0,       100.0,    100.0,   10.0])

    # For experiment
            # x,    y,    vx,   vy,     ax,   ay,  psi,    psidot
    Q = diag([1e-8, 1e-8, 2e-4, 2e-4,   2.,   2.,  2e-4,   2.])
            # x_meas, y_meas, vel_meas, psiDot_meas, a_x_meas, a_y_meas, vy_meas   
    R = diag([10.,    10.,    0.1,      0.1,         10.,      10.,      1.])

    # For simulation
    Q = diag(ones(8))
    R = diag(ones(7))

    t0 = rospy.get_rostime().to_sec()
    imu = ImuClass(t0)
    gps = GpsClass(t0)
    enc = EncClass(t0)
    ecu = EcuClass(t0)
    est = Estimator(t0,loop_rate,a_delay,df_delay,Q,R)
    
    track = Track(0.01,0.8)
    track.createRaceTrack()
    estMsg = pos_info()
    
    while not rospy.is_shutdown():
        
        est.estimateState(imu,gps,enc,ecu)

        estMsg.s, estMsg.ey, estMsg.epsi = track.Localize(est.x_est, est.y_est, est.yaw_est)
        
        estMsg.header.stamp = rospy.get_rostime()
        estMsg.v        = np.sqrt(est.vx_est**2 + est.vy_est**2)
        estMsg.x        = est.x_est 
        estMsg.y        = est.y_est
        estMsg.v_x      = est.vx_est 
        estMsg.v_y      = est.vy_est
        estMsg.psi      = est.yaw_est
        estMsg.psiDot   = est.psiDot_est
        estMsg.a_x      = est.ax_est
        estMsg.a_y      = est.ay_est
        estMsg.u_a      = ecu.a
        estMsg.u_df     = ecu.df

        est.state_pub_pos.publish(estMsg)

        imu.saveHistory()
        gps.saveHistory()
        enc.saveHistory()
        ecu.saveHistory()
        est.saveHistory()
        est.rate.sleep()

    homedir = os.path.expanduser("~")
    pathSave = os.path.join(homedir,"barc_debugging/estimator_output.npz")
    np.savez(pathSave,yaw_est_his       = est.yaw_est_his,
                      psiDot_est_his    = est.psiDot_est_his,
                      x_est_his         = est.x_est_his,
                      y_est_his         = est.y_est_his,
                      vx_est_his        = est.vx_est_his,
                      vy_est_his        = est.vy_est_his,
                      ax_est_his        = est.ax_est_his,
                      ay_est_his        = est.ay_est_his,
                      estimator_time    = est.time_his)

    pathSave = os.path.join(homedir,"barc_debugging/estimator_imu.npz")
    np.savez(pathSave,psiDot_his    = imu.psiDot_his,
                      roll_his      = imu.roll_his,
                      pitch_his     = imu.pitch_his,
                      yaw_his       = imu.yaw_his,
                      ax_his        = imu.ax_his,
                      ay_his        = imu.ay_his,
                      imu_time      = imu.time_his)

    pathSave = os.path.join(homedir,"barc_debugging/estimator_gps.npz")
    np.savez(pathSave,x_his         = gps.x_his,
                      y_his         = gps.y_his,
                      gps_time      = gps.time_his)

    pathSave = os.path.join(homedir,"barc_debugging/estimator_enc.npz")
    np.savez(pathSave,v_fl_his          = enc.v_fl_his,
                      v_fr_his          = enc.v_fr_his,
                      v_rl_his          = enc.v_rl_his,
                      v_rr_his          = enc.v_rr_his,
                      enc_time          = enc.time_his)

    pathSave = os.path.join(homedir,"barc_debugging/estimator_ecu.npz")
    np.savez(pathSave,a_his         = ecu.a_his,
                      df_his        = ecu.df_his,
                      ecu_time      = ecu.time_his)

    print "Finishing saveing state estimation data"


class Estimator(object):
    """ Object collecting  estimated state data
    Attributes:
        Estimated states:
            1.x_est     2.y_est
            3.vx_est    4.vy_est        5.v_est
            6.ax_est    7.ay_est
            8.yaw_est   9.psiDot_est    10.psiDrift_est
        Estimated states history:
            1.x_est_his     2.y_est_his
            3.vx_est_his    4.vy_est_his        5.v_est_his
            6.ax_est_his    7.ay_est_his
            8.yaw_est_his   9.psiDot_est_his    10.psiDrift_est_his
        Time stamp
            1.t0 2.time_his 3.curr_time
    Methods:
        stateEstimate(imu,gps,enc,ecu):
            Estimate current state from sensor data
        ekf(y,u):
            Extended Kalman filter
        numerical_jac(func,x,u):
            Calculate jacobian numerically
        f(x,u):
            System prediction model
        h(x,u):
            System measurement model

    """

    def __init__(self,t0,loop_rate,a_delay,df_delay,Q,R):
        """ Initialization
        Arguments:
            t0: starting measurement time

        """
        dt          = 1.0 / loop_rate
        self.rate   = rospy.Rate(loop_rate)
        L_f         = rospy.get_param("L_a")       # distance from CoG to front axel
        L_r         = rospy.get_param("L_b")       # distance from CoG to rear axel
        self.vhMdl  = (L_f, L_r)
        self.Q      = Q
        self.R      = R
        self.P      = np.eye(np.size(Q,0)) # initializationtial covariance matrix
        self.z_EKF  = np.zeros(np.size(Q,0)) # initial state mean
        self.dt     = dt
        self.a_delay        = a_delay
        self.df_delay       = df_delay
        self.a_his          = [0.0]*int(a_delay/dt)
        self.df_his         = [0.0]*int(df_delay/dt)
        self.state_pub_pos  = rospy.Publisher('pos_info', pos_info, queue_size=1)
        self.t0             = t0

        self.x_est          = 0.0
        self.y_est          = 0.0
        self.vx_est         = 0.0
        self.vy_est         = 0.0
        self.v_est          = 0.0
        self.ax_est         = 0.0
        self.ay_est         = 0.0
        self.yaw_est        = 0.0
        self.psiDot_est     = 0.0
        self.psiDrift_est   = 0.0
        self.curr_time      = 0.0

        self.x_est_his          = [0.0]
        self.y_est_his          = [0.0]
        self.vx_est_his         = [0.0]
        self.vy_est_his         = [0.0]
        self.v_est_his          = [0.0]
        self.ax_est_his         = [0.0]
        self.ay_est_his         = [0.0]
        self.yaw_est_his        = [0.0]
        self.psiDot_est_his     = [0.0]
        self.time_his           = [0.0]

    # ecu command update
    def estimateState(self,imu,gps,enc,ecu):
        """Do extended Kalman filter to estimate states"""
        self.curr_time = rospy.get_rostime().to_sec() - self.t0

        self.a_his.append(ecu.a)
        self.df_his.append(ecu.df)

        u = [self.a_his.pop(0), self.df_his.pop(0)]
        bta = 0.5 * u[1]

        y = np.array([gps.x, gps.y, enc.v_meas, imu.psiDot, imu.ax, imu.ay, sin(bta)*enc.v_meas])

        self.ekf(y,u)


    def ekf(self, y, u):
        """
        EKF   Extended Kalman Filter for nonlinear dynamic systems
        ekf(f,mx,P,h,z,Q,R) returns state estimate, x and state covariance, P 
        for nonlinear dynamic system:
                  x_k+1 = f(x_k) + w_k
                  y_k   = h(x_k) + v_k
        where w ~ N(0,Q) meaning w is gaussian noise with covariance Q
              v ~ N(0,R) meaning v is gaussian noise with covariance R
        Inputs:    f: function handle for f(x)
                   z_EKF: "a priori" state estimate
                   P: "a priori" estimated state covariance
                   h: fanction handle for h(x)
                   y: current measurement
                   Q: process noise covariance 
                   R: measurement noise covariance
                   args: additional arguments to f(x, *args)
        Output:    mx_kp1: "a posteriori" state estimate
                   P_kp1: "a posteriori" state covariance
                   
        Notation: mx_k = E[x_k] and my_k = E[y_k], where m stands for "mean of"
        """
        
        xDim    = self.z_EKF.size                           # dimension of the state
        mx_kp1  = self.f(self.z_EKF, u)                     # predict next state
        A       = self.numerical_jac(self.f, self.z_EKF, u) # linearize process model about current state
        P_kp1   = dot(dot(A,self.P),A.T) + self.Q           # proprogate variance
        my_kp1  = self.h(mx_kp1, u)                              # predict future output
        H       = self.numerical_jac(self.h, mx_kp1, u)     # linearize measurement model about predicted next state
        P12     = dot(P_kp1, H.T)                           # cross covariance
        K       = dot(P12, inv( dot(H,P12) + self.R))       # Kalman filter gain
        
        self.z_EKF  = mx_kp1 + dot(K,(y - my_kp1))
        self.P      = dot(dot(K,self.R),K.T) + dot( dot( (eye(xDim) - dot(K,H)) , P_kp1)  ,  (eye(xDim) - dot(K,H)).T )

        (self.x_est, self.y_est, self.vx_est, self.vy_est, self.ax_est, self.ay_est, self.yaw_est, self.psiDot_est) = self.z_EKF
    

    def numerical_jac(self,func,x,u):
        """
        Function to compute the numerical jacobian of a vector valued function 
        using final differences
        """
        # numerical gradient and diagonal hessian
        y = func(x,u)
        
        jac = zeros( (y.size,x.size) )
        eps = 1e-5
        xp = np.copy(x)
        
        for i in range(x.size):
            xp[i] = x[i] + eps/2.0
            yhi = func(xp, u)
            xp[i] = x[i] - eps/2.0
            ylo = func(xp, u)
            xp[i] = x[i]
            jac[:,i] = (yhi - ylo) / eps
        return jac

    def f(self, z, u):
        """ This Sensor model contains a pure Sensor-Model and a Kinematic model. They're independent from each other."""
        (l_A,l_B) = self.vhMdl
        bta = math.atan2(l_A*tan(u[1]),l_A+l_B)
        dt = self.dt
        zNext = [0]*8
        zNext[0] = z[0] + dt*(cos(z[6])*z[2] - sin(z[6])*z[3])  # x
        zNext[1] = z[1] + dt*(sin(z[6])*z[2] + cos(z[6])*z[3])  # y
        zNext[2] = z[2] + dt*(z[4]+z[7]*z[3])                   # v_x
        zNext[3] = z[3] + dt*(z[5]-z[7]*z[2])                   # v_y
        zNext[4] = z[4]                                         # a_x
        zNext[5] = z[5]                                         # a_y
        zNext[6] = z[6] + dt*(z[7])                             # psi
        zNext[7] = z[7]                                         # psidot
        return np.array(zNext)

    def h(self, x, u):
        """ This is the measurement model to the kinematic<->sensor model above """
        y = [0]*7
        y[0] = x[0]   # x
        y[1] = x[1]   # y
        y[2] = x[2]   # vx
        y[3] = x[7]   # psiDot
        y[4] = x[4]   # a_x
        y[5] = x[5]   # a_y
        y[6] = x[3]   # vy
        return np.array(y)

    def saveHistory(self):
        self.time_his.append(self.curr_time)

        self.x_est_his.append(self.x_est)
        self.y_est_his.append(self.y_est)
        self.vx_est_his.append(self.vx_est)
        self.vy_est_his.append(self.vy_est)
        self.v_est_his.append(self.v_est)
        self.ax_est_his.append(self.ax_est)
        self.ay_est_his.append(self.ay_est)
        self.yaw_est_his.append(self.yaw_est)
        self.psiDot_est_his.append(self.psiDot_est)

class ImuClass(object):
    """ Object collecting GPS measurement data
    Attributes:
        Measurement:
            1.yaw 2.psiDot 3.ax 4.ay 5.roll 6.pitch
        Measurement history:
            1.yaw_his 2.psiDot_his 3.ax_his 4.ay_his 5.roll_his 6.pitch_his
        Time stamp
            1.t0 2.time_his
    """
    def __init__(self,t0):
        """ Initialization
        Arguments:
            t0: starting measurement time
        """

        rospy.Subscriber('imu/data', Imu, self.imu_callback, queue_size=1)

        # Imu measurement
        self.yaw     = 0.0
        self.psiDot  = 0.0
        self.ax      = 0.0
        self.ay      = 0.0
        self.roll    = 0.0
        self.pitch   = 0.0
        
        # Imu measurement history
        self.yaw_his     = [0.0]
        self.psiDot_his  = [0.0]
        self.ax_his      = [0.0]
        self.ay_his      = [0.0]
        self.roll_his    = [0.0]
        self.pitch_his   = [0.0]
        
        # time stamp
        self.t0          = t0
        self.time_his    = [0.0]

        # Time for yawDot integration
        self.curr_time = 0.0
        self.prev_time = 0.0


    def imu_callback(self,data):
        """Unpack message from sensor, IMU"""
        
        self.curr_time = rospy.get_rostime().to_sec() - self.t0

        if self.prev_time > 0:
            self.yaw += self.psiDot * (self.curr_time-self.prev_time)

        ori = data.orientation
        quaternion = (ori.x, ori.y, ori.z, ori.w)
        (roll_raw, pitch_raw, dummy) = transformations.euler_from_quaternion(quaternion)
        self.roll   = roll_raw
        self.pitch  = pitch_raw

        w_z = data.angular_velocity.z
        a_x = data.linear_acceleration.x
        a_y = data.linear_acceleration.y
        a_z = data.linear_acceleration.z

        self.psiDot = w_z
        self.ax = cos(-pitch_raw)*a_x + sin(-pitch_raw)*sin(-roll_raw)*a_y - sin(-pitch_raw)*cos(-roll_raw)*a_z
        self.ay = cos(-roll_raw)*a_y + sin(-roll_raw)*a_z

        self.prev_time = self.curr_time


    def saveHistory(self):
        """ Save measurement data into history array"""

        self.time_his.append(self.curr_time)
        
        self.yaw_his.append(self.yaw)
        self.psiDot_his.append(self.psiDot)
        self.ax_his.append(self.ax)
        self.ay_his.append(self.ay)
        self.roll_his.append(self.roll)
        self.pitch_his.append(self.pitch)



class GpsClass(object):
    """ Object collecting GPS measurement data
    Attributes:
        Measurement:
            1.x 2.y
        Measurement history:
            1.x_his 2.y_his
        Time stamp
            1.t0 2.time_his 3.curr_time
    """
    def __init__(self,t0):
        """ Initialization
        Arguments:
            t0: starting measurement time
        """

        rospy.Subscriber('hedge_imu_fusion', hedge_imu_fusion, self.gps_callback, queue_size=1)

        # GPS measurement
        self.x      = 0.0
        self.y      = 0.0
        
        # GPS measurement history
        self.x_his  = np.array([0.0])
        self.y_his  = np.array([0.0])
        
        # time stamp
        self.t0         = t0
        self.time_his   = np.array([0.0])
        self.curr_time  = 0.0

    def gps_callback(self,data):
        """Unpack message from sensor, GPS"""
        self.curr_time = rospy.get_rostime().to_sec() - self.t0

        self.x = data.x_m
        self.y = data.y_m

        # 1) x(t) ~ c0x + c1x * t + c2x * t^2
        # 2) y(t) ~ c0y + c1y * t + c2y * t^2
        # c_X = [c0x c1x c2x] and c_Y = [c0y c1y c2y] 
        # n_intplt = 20
        # if size(self.x_his,0) > n_intplt: # do interpolation when there is enough points
        #     x_intplt = self.x_his[-n_intplt:]
        #     y_intplt = self.y_his[-n_intplt:]
        #     t_intplt = self.time_his[-n_intplt:]
        #     t_matrix = vstack([t_intplt**2, t_intplt, ones(sz)]).T
        #     self.c_X = linalg.lstsq(t_matrix, x_intplt)[0]
        #     self.c_Y = linalg.lstsq(t_matrix, y_intplt)[0]
        #     self.x = polyval(self.c_X, self.curr_time)
        #     self.y = polyval(self.c_Y, self.curr_time)

    def saveHistory(self):
        self.time_his = np.append(self.time_his,self.curr_time)

        self.x_his = np.append(self.x_his,self.x)
        self.y_his = np.append(self.y_his,self.y)


class EncClass(object):
    """ Object collecting ENC measurement data
    Attributes:
        Measurement:
            1.v_fl 2.v_fr 3. v_rl 4. v_rr
        Measurement history:
            1.v_fl_his 2.v_fr_his 3. v_rl_his 4. v_rr_his
        Time stamp
            1.t0 2.time_his 3.curr_time
    """
    def __init__(self,t0):
        """ Initialization
        Arguments:
            t0: starting measurement time
        """
        rospy.Subscriber('vel_est', Vel_est, self.enc_callback, queue_size=1)

        # ENC measurement
        self.v_fl      = 0.0
        self.v_fr      = 0.0
        self.v_rl      = 0.0
        self.v_rr      = 0.0
        self.v_meas    = 0.0
        
        # ENC measurement history
        self.v_fl_his    = [0.0]
        self.v_fr_his    = [0.0]
        self.v_rl_his    = [0.0]
        self.v_rr_his    = [0.0]
        self.v_meas_his  = [0.0]
        
        # time stamp
        self.v_count    = 0
        self.v_prev     = 0.0
        self.t0         = t0
        self.time_his   = [0.0]
        self.curr_time  = 0.0

    def enc_callback(self,data):
        """Unpack message from sensor, ENC"""
        self.curr_time = rospy.get_rostime().to_sec() - self.t0

        self.v_fl = data.vel_fl
        self.v_fr = data.vel_fr
        self.v_rl = data.vel_bl
        self.v_rr = data.vel_br
        v_est = (self.v_rl + self.v_rr)/2
        if v_est != self.v_prev:
            self.v_meas = v_est
            self.v_prev = v_est
            self.v_count = 0
        else:
            self.v_count += 1
            if self.v_count > 10:     # if 10 times in a row the same measurement
                self.v_meas = 0       # set velocity measurement to zero

    def saveHistory(self):
        self.time_his.append(self.curr_time)
        
        self.v_fl_his.append(self.v_fl)
        self.v_fr_his.append(self.v_fr)
        self.v_rl_his.append(self.v_rl)
        self.v_rr_his.append(self.v_rr)

        self.v_meas_his.append(self.v_meas)

class EcuClass(object):
    """ Object collecting CMD command data
    Attributes:
        Input command:
            1.a 2.df
        Input command history:
            1.a_his 2.df_his
        Time stamp
            1.t0 2.time_his 3.curr_time
    """
    def __init__(self,t0):
        """ Initialization
        Arguments:
            t0: starting measurement time
        """
        rospy.Subscriber('ecu', ECU, self.ecu_callback, queue_size=1)

        # ECU measurement
        self.a  = 0.0
        self.df = 0.0
        
        # ECU measurement history
        self.a_his  = [0.0]
        self.df_his = [0.0]
        
        # time stamp
        self.t0         = t0
        self.time_his   = [0.0]
        self.curr_time  = 0.0

    def ecu_callback(self,data):
        """Unpack message from sensor, ECU"""
        self.curr_time = rospy.get_rostime().to_sec() - self.t0

        self.a  = data.motor
        self.df = data.servo

    def saveHistory(self):
        self.time_his.append(self.curr_time)
        
        self.a_his.append(self.a)
        self.df_his.append(self.df)

if __name__ == '__main__':
    try:
        main()
    except rospy.ROSInterruptException:
        pass