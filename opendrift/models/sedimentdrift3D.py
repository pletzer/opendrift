# This file is part of OpenDrift.
#
# OpenDrift is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 2
#
# OpenDrift is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with OpenDrift.  If not, see <http://www.gnu.org/licenses/>.
#
# Copyright 2015, Knut-Frode Dagestad, MET Norway
# Copyright 2018, Simon, MetOcean Solutions Ltd.

import numpy as np
import logging; logger = logging.getLogger(__name__)
from opendrift.models.oceandrift import OceanDrift
from opendrift.models.oceandrift import Lagrangian3DArray


class SedimentElement(Lagrangian3DArray):
    # Lagrangian3DArray has already the variables terminal_velocity, and wind_drift_factor
    variables = Lagrangian3DArray.add_variables([
        ('settled', {'dtype': np.int16,  # 0 is active, 1 is settled
                     'units': '1',
                     'default': 0}),
        ('age_seconds', {'dtype': np.float32,
                 'units': 's',
                 'default': 0}),
        ('terminal_velocity', {'dtype': np.float32,
                               'units': 'm/s',
                               'default': -0.001}), # 1 mm/s negative buoyancy
        ('critical_shear_stress', {'dtype': np.float32,
                                  'units': 'N/m2',
                                  'default': 0.0})
        ])

class SedimentDrift3D(OceanDrift): # based on OceanDrift base class
    """Trajectory model based on the OpenDrift framework using the OceanDrift baseclass

    Sediment 3D motion 
    Propagation with horizontal and vertical ocean currents, horizontal and 
    vertical diffusions (additional wind drag inherited from base class but probably not relevant here).
    Suitable for sediment tracers, e.g. for tracking sediment particles.
    Adapted from OceanDrift by Simon Weppe - MetOcean Solutions.

    """
    ElementType = SedimentElement 

    required_variables = {
        'x_sea_water_velocity': {'fallback': 0},
        'y_sea_water_velocity': {'fallback': 0},
        'upward_sea_water_velocity': {'fallback': 0},
        'x_wind': {'fallback': 0},
        'y_wind': {'fallback': 0},
        'sea_surface_wave_stokes_drift_x_velocity': {'fallback': 0},
        'sea_surface_wave_stokes_drift_y_velocity': {'fallback': 0},
        'sea_surface_wave_significant_height' : {'fallback': 0},
        'sea_surface_wave_period_at_variance_spectral_density_maximum': {'fallback': 0},
        'sea_surface_wave_mean_period_from_variance_spectral_density_second_frequency_moment': {'fallback': 0},
        'land_binary_mask': {'fallback': None},
        'ocean_vertical_diffusivity': {'fallback': 0.02, 'profiles': True},
        'sea_floor_depth_below_sea_level': {'fallback': 0},
        }
    
    # Adding some specs - inspired from basereader.py
    #
    # Default plotting colors of trajectory endpoints

    status_colors_default = {'initial': 'green',
                             'active': 'blue',
                             'missing_data': 'gray',
                             'settled': 'red'}
    
    # The depth range (in m) which profiles shall cover
    required_profiles_z_range = [-20, 0]

    def __init__(self, *args, **kwargs):

        # Calling general constructor of parent class
        super(SedimentDrift3D, self).__init__(*args, **kwargs)
        
        # resuspension (switched off by default)
        self._add_config({
            'drift:resuspension': {'type': 'bool', 'default': False,
                # 'min': 0, 'max': 1e10, 'units': '-',
                'description': 'switch to activate/deactivate resuspension',
                'level': self.CONFIG_LEVEL_ESSENTIAL}})

        # By default, sediments do strand at coastline
        self._set_config_default('general:coastline_action', 'stranding')
        # Vertical mixing is enabled as default
        self._set_config_default('drift:vertical_mixing', True)
        # Vertical advection switched off by default (if w is available)
        self._set_config_default('drift:vertical_advection', False)
        # Settling on seafloor : if no resuspension (default) : deactivate settled particles 
        #                        if resuspension is on, will be set to 'lift_to_seafloor'
        #                        other options : ['none', 'lift_to_seafloor', 'deactivate', 'previous']
        self._set_config_default('general:seafloor_action', 'deactivate') 

        self.max_speed = 5.0

    def update_terminal_velocity(self, Tprofiles=None, Sprofiles=None,
                                 z_index=None):
        """Calculate terminal velocity due to bouyancy from own properties
        and environmental variables. Sub-modules should overload
        this method for particle-specific behaviour

        Same as OceanDrift for now - could include more sophisticated models based on temp/salinity etc..
        >> Maybe add some uncertainities to particle settling velocities ?
        """
        pass 

    def update(self):
        """Update positions and properties of elements."""
        self.elements.age_seconds += self.time_step.total_seconds()

        # Simply move particles with ambient current
        self.advect_ocean_current() # from physics_methods.py

        # Advect particles due to wind drag
        # (according to specified wind_drift_factor)
        self.advect_wind()

        # Stokes drift
        self.stokes_drift()
        
        # Turbulent Mixing
        if self.get_config('drift:vertical_mixing') is True:
            self.update_terminal_velocity()  # routine to estimate settling velocity - simply keeps the user-input one for now 
            self.vertical_mixing()
        else:  # Buoyancy
            self.update_terminal_velocity() # routine to estimate settling velocity - simply keeps the user-input one for now 
            self.vertical_buoyancy()

        # Vertical advection
        self.vertical_advection()

        # Sediment resuspension checks , if switched on
        self.sediment_resuspension() #-

        # Deactivate elements that exceed a certain age
        if self.get_config('drift:max_age_seconds') is not None:
            self.deactivate_elements(self.elements.age_seconds >=
                                     self.get_config('drift:max_age_seconds'),
                                     reason='retired')
        
        # >> not needed anymore > will be flagged as reason = 'seafloor' within OceanDrift parent 
        if False:
            # When no resuspension is required, deactivate particles that reached the seabed, this could probably be moved to a bottom_interaction()
            if self.get_config('drift:resuspension') is False:
                self.deactivate_elements(self.elements.z ==
                                         -1.*self.environment.sea_floor_depth_below_sea_level,
                                         reason='settled')

        # Note the interaction with shoreline in taken care of by interact_with_coastline in basemodel.py
        # when run() is called

    def sediment_resuspension(self):
        """
        Compute ambient bed shear stresses at positions of particles that are (or just touched) the bottom
        and determine if they can settle or be resuspended based on critical_shear_stress 
        """

        # > use the element attribute "moving" to freeze/unfreeze settled particles (see in advect_ocean_current())
        # > move them back only when bedshearstress > critical_shear_stress

        # 1-find particles on the bottom - done in bottom_interaction() 
        # 2-compute bed shear stresses 
        # 3-compare to critical_shear_stress
        # 4-resuspend or stay on seabed depending on 3)
        #   > probably need to use a cut-off age after which particles are de-activated anyway
        #   to prevent excessive build-up of "active" particle in the simulations
        if self.get_config('drift:resuspension') is True:
            self.set_config('general:seafloor_action', 'lift_to_seafloor') # we need this to
            logger.debug('Resuspension physics included : drift:resuspension == True')
            # 1. find particles that touched the seafloor
            #   >> identified using self.elements.moving = 0/1 which is set in bottom_interaction(),  within vertical_mixing()
            # 2-compute bed shear stresses at particle locations
            tau_cw,tau_cw_max = self.bedshearstress_current_wave()
            # compare them with critical bed shearstresses
            to_resuspend = (np.logical_and(self.elements.moving == 0,tau_cw_max>self.elements.critical_shear_stress))
            if np.sum(to_resuspend) > 0 :
                logger.debug('Resuspending %s elements where tau_cw_max>critical_shear_stress' % np.sum(to_resuspend))
                sea_floor_depth = self.sea_floor_depth()
                # Resuspend 1 cm above seafloor
                self.elements.z[to_resuspend] = -sea_floor_depth[to_resuspend] + 0.01
                # Allow moving again
                self.elements.moving[to_resuspend] = 1
            else:              
              logger.debug('No elements to resuspend (tau_cw_max < critical_shear_stress everywhere')
 
    def bottom_interaction(self, seafloor_depth):
        """Sub method of vertical_mixing, determines settling"""
        # Elements at or below seafloor are settled, by setting
        # self.elements.moving to 0.
        # These elements will not move until eventual later resuspension.

        sea_floor_depth =  self.sea_floor_depth()
        below = np.where(self.elements.z < -sea_floor_depth)[0]
        self.elements.z[below] = -sea_floor_depth[below]
        settling = np.logical_and(self.elements.z <= seafloor_depth, self.elements.moving==1)
        if np.sum(settling) > 0:
            logger.debug('Settling %s elements at seafloor' % np.sum(settling))
            self.elements.moving[settling] = 0

    def resuspension(self):
        """Resuspending elements if current speed > .5 m/s"""
        resuspending = np.logical_and(self.current_speed()>.5, self.elements.moving==0)
        if np.sum(resuspending) > 0:
            # Allow moving again
            self.elements.moving[resuspending] = 1
            # Suspend 1 cm above seafloor
            self.elements.z[resuspending] = self.elements.z[resuspending] + .01

# General physics functions---------------------------------------------------------------------------------
# Could be moved to physics_methods.py once cross-checked / accepted 


    def bedshearstress_current_wave(self):
        """
        Computation of bed shear stress due to current and waves
        current-related stress is computed following a drag-coefficient approach
        wave-related stress is computed following Van Rijn approach
        combined wave-current mean and max stresses are computed following Soulsby(1995) approach

        https://odnature.naturalsciences.be/coherens/manual#manual
        https://odnature.naturalsciences.be/downloads/coherens/documentation/chapter7.pdf#nameddest=Bed_shear_stresses
        
        http://www.coastalwiki.org/wiki/Shallow-water_wave_theory#
        http://www.coastalwiki.org/wiki/Shallow-water_wave_theory#Seabed_Friction  

        General relationships obtained from :
        https://repository.tudelft.nl/islandora/object/uuid%3Aea12eb20-aee3-4f58-99fb-ebc216e98879
        Description of TRANSPOR2004 and Implementation in Delft3D-ONLINE
        """

        rho_water = 1027 # kg/m3
        z0 = 0.001 # roughness height - hard-coded constant for now 
        water_depth = np.abs(self.sea_floor_depth()) # water depth positive down
        current_speed = self.current_speed() # depth-averaged current 

        #######################################################
        # current-related bed shear stress
        #######################################################

        # depth-averaged current approach :
        if True : # current data fron reader is depth-averaged
            Cdrag=( 0.4 /(np.log(abs(water_depth/z0))-1) )**2
            #Now compute the bed shear stress [N/m2] 
            tau_cur=rho_water*Cdrag*current_speed**2
        else:
            # 3D currents - to implement
            last_wet_bin_depth = 0.0 
            Cdrag=( 0.4 /(np.log(abs(last_wet_bin_depth/z0))-1) )**2
            #Now compute the bed shear stress [N/m2] 
            tau_cur=rho_water*Cdrag*current_speed**2        

        #######################################################
        # wave-related bed shear stress (if wave available)
        #######################################################
        hs = self.significant_wave_height()
        tp = self.wave_period()
        # wave-related roughness

        # see VanRijn 
        # https://tinyurl.com/nyjcss5w
        # SIMPLE GENERAL FORMULAE FOR SAND TRANSPORT IN RIVERS, ESTUARIES AND COASTAL WATERS
        # >> page 6
        # 
        # Note : VanRijn 2007 suggests same equations than for current-related roughness 
        # where 20*d50 <ksw<150*d50
        # here we are using Nikuradse roughness for consistency with the 
        # use of z0 in the current-related shear stress 

        ksw=30*z0 # wave related bed roughness - taken as Nikuradse roughness 
        w=2*np.pi/tp
        kh = qkhfs( w, water_depth ) # dispersion relationship 
        Adelta = hs/(2*np.sinh(kh)) # peak wave orbital excursion 
        Udelta = (np.pi*hs)/(tp*np.sinh(kh))  # peak wave orbital velocity linear theory 

        fw_swart = np.exp(-5.977+5.213*(Adelta/ksw)**-0.194)  # wave-related friction coefficient (Swart,1974) eq. 3.8 on VanRijn pdf
        fw_swart = np.minimum(fw_swart,0.3)
        fw_soulsby = 0.237 * (Adelta/ksw)**-0.52 #eq. 7.18 COHERENS, not used for now
        tau_wave = 0.25 * rho_water * fw_swart * (Udelta)**2 # wave-related bed shear stress eq. 3.7 on VanRijn pdf
        #cycle mean bed shear stress according to Soulsby,1995, see also COHERENS manual eq. 7.14
        tau_cw=tau_cur*[1+1.2*(tau_wave/(tau_cur+tau_wave))**3.2]
        # max bed shear stress during wave cycle - in theory should be used for the resuspension criterion.
        theta_cur_dir = 0.0 #angle between direction of travel of wave and current, in radians, in practice rarely known...take 0 ?
        # tau_max = ( (tau_cur + tau_wave*np.cos(theta_cur_dir))**2 + (tau_wave*np.sin(theta_cur_dir))**2 )**0.5 
        tau_cw_max = (tau_cur**2 + tau_wave**2 + 2*tau_cur*tau_wave*np.cos(theta_cur_dir))**0.5 # COHERENS eq. 7.15
        
        return tau_cw[0],tau_cw_max 


#from  https://github.com/csherwood-usgs/crspy/blob/master/crspy.py
def qkhfs( w, h ):
    """
    Quick iterative calculation of kh in gravity-wave dispersion relationship
    kh = qkhfs(w, h )
    
    Input
        w - angular wave frequency = 2*pi/T where T = wave period [1/s]
        h - water depth [m]
    Returns
        kh - wavenumber * depth [ ]
    Orbital velocities from kh are accurate to 3e-12 !
    RL Soulsby (2006) "Simplified calculation of wave orbital velocities"
    HR Wallingford Report TR 155, February 2006
    Eqns. 12a - 14
    """
    g = 9.81
    x = w**2.0 *h/g
    y = np.sqrt(x) * (x<1.) + x *(x>=1.)
    # is this faster than a loop?
    t = np.tanh( y )
    y = y-( (y*t -x)/(t+y*(1.0-t**2.0)))
    t = np.tanh( y )
    y = y-( (y*t -x)/(t+y*(1.0-t**2.0)))
    t = np.tanh( y )
    y = y-( (y*t -x)/(t+y*(1.0-t**2.0)))
    kh = y
    return kh

def bedshearstress_cw_ERCORE(self,p,time=None,imax=2):
    """Computation of bed shear stress due to current and waves
    current-related stress is computed following a drag-coefficient approach
    wave-related stress is computed following Van Rijn approach
    combined wave-current mean and max stresses are computed followin Soulsby(1995) approach

    https://odnature.naturalsciences.be/coherens/manual#manual

    https://odnature.naturalsciences.be/downloads/coherens/documentation/chapter7.pdf#nameddest=Bed_shear_stresses
    
    http://www.coastalwiki.org/wiki/Shallow-water_wave_theory#
    http://www.coastalwiki.org/wiki/Shallow-water_wave_theory#Seabed_Friction


    Arguments:
      self : Material object, expected to include fields movers, reactors (if input)
      p: particle positions array (Nx3)
      time: 
    Returns:
      tau_cur : current-related bed shear stress tau_cur
      tau_cw : combined mean current-wave bed shear stress 
      tau_max: combined max current-wave bed shear stress tau_max
      topo : water depth at particle positions (of first mover) 
    """
    rhow=1027 # default volumic mass for seawater
    tau_cur=numpy.tile(0.0,numpy.size(p,0)) # allocate
    tau_cw=numpy.tile(0.0,numpy.size(p,0)) # allocate
    tau_max=numpy.tile(0.0,numpy.size(p,0)) # allocate
    # current-related bed shear stress (sum of all movers)
    for mover in self.movers[0:]:
      if mover.topo: # topo needed to define bedshear stress
        topo=mover.topo.interp(p,None,3)     
        if (not mover.is3d) and (mover.z0>0): # mover is a 2D-depth averaged current
          # temporarily set mover.z0 to 0.0 so that mover.interp yields the un-corrected depth-averaged current (direct interpolation, no log profile )
          # not super elegant, probably a better way to do this - anyway to access GriddedTide or GriddedData from here ?
          z0_tmp=copy.copy(mover.z0)
          mover.z0=0.0
          u2dhim=mover.interp(p,time,imax)   #u2dhim=mover.interp(self,p,time,imax)        
          mover.z0=z0_tmp
          u2dhim_mag=(u2dhim[:,0]**2+u2dhim[:,1]**2)**0.5
          # Drag coefficient for 2D case using water depth and z0 (see COHERENS manual eq.7.2, or Delft3d)
          Cdrag=( 0.4 /(numpy.log(abs(topo[:,0] /mover.z0))-1) )**2
          #Now compute the bed shear stress [N/m2] 
          tau_cur+=rhow*Cdrag*u2dhim_mag**2             
        elif (mover.is3d) and (mover.z0>0):   # mover is a 3D current field
          #import pdb;pdb.set_trace()
          # Assume the first grid point above the bed is assumed to be the top of the logarithmic boundary layer
          # the log profile extends from that last wet bin level, to the bottom
          # see COHERENS manual eq 7.1/7.2
  
          # find closest "wet" vertical levels at each particle locations
          bin_lev=numpy.zeros(len(p[:,0]))
          for lev in mover.lev:
            bin_lev[topo[:,0]<=lev]=lev
          #vertical height from last wet vertical bin to seabed
          zb=bin_lev-topo[:,0]
          #current computed at last wet vertical bin
          uub=mover.interp(numpy.vstack((p[:,0],p[:,1],bin_lev)).T,time,imax)
          uub_mag=(uub[:,0]**2+uub[:,1]**2)**0.5
          # Drag coefficient for 3D case using zb and z0 (see COHERENS manual eq.7.2, or Delft3d)
          Cdrag=( 0.4 /(numpy.log(abs(zb /mover.z0))-1) )**2 
         #Now compute the bed shear stress [N/m2]
          tau_cur+=rhow*Cdrag*uub_mag**2

    # wave-related bed shear stress
    # http://www.coastalwiki.org/wiki/Shallow-water_wave_theory#Seabed_Friction
    
    if len(self.reactors)>0: # check if wave forcing is included 
      # for now assume that if reactors exist, they will be correctly input
      # computation of wave-related and combined bed shear stresses based on code from 
      #https://svn.oss.deltares.nl/repos/openearthtools/trunk/matlab/general/phys_fun/bedshearstresses.m 
      #https://svn.oss.deltares.nl/repos/openearthtools/trunk/matlab/general/phys_fun/sandandmudtransport.m
      hs=self.reactors['hs'].interp(p[:1,:],time)[:,0] #wave height
      tp=self.reactors['tp'].interp(p[:1,:],time)[:,0] #wave period
      # wave-related roughness
      # vanRijn 2007 suggests same equations than for current-related roughness where 20* d50 <ksw<150*d50
      # here we are using nikuradse for consistency with the use of z0 in the mover class for now
      ksw=30*self.movers[0].z0  
      topo= self.movers[0].topo.interp(p,None,3)
      w=2*numpy.pi/tp
      kh = qkhfs( w, topo[:,0] ) # dispersion relationship
      Adelta = hs/(2*numpy.sinh(kh)) # peak wave orbital excursion
      Udelta = (numpy.pi*hs)/(tp*numpy.sinh(kh))  # peak wave orbital velocity
      fw = numpy.exp(-5.977+5.213*(Adelta/ksw)**-0.194)  # wave-related friction coefficient (van Rijn)
      fw = numpy.min(fw,0.3)
      tau_wave = 0.25 * rhow * fw * (Udelta)**2 # wave-related bed shear stress
      #cycle mean bed shear stress according to Soulsby,1995, see also COHERENS manual eq. 7.14
      tau_cw=tau_cur*[1+1.2*(tau_wave/(tau_cur+tau_wave))**3.2]
      # max bed shear stress during wave cycle
      theta_cur_dir = 0 #angle between direction of travel of wave and current, in radians
      tau_max = tau_cur + tau_wave*np.cos(theta_cur_dir) + tau_wave*np.sin(theta_cur_dir)
      tau_max=[tau_wave**2+tau_cur**2]**0.5 
    else:
      tau_max=tau_cur
      tau_cw=tau_cur
    # if (tau_cur==0).any():
    #   import pdb;pdb.set_trace()
    return tau_cur,tau_cw,tau_max,topo

