# This file has been adapted from pelagicegg.py but introduces a settlement competency period
# added config setting for 'min_settlement_age_seconds' which defines the minimum age after which  settlement can occur.
# Coastal settlement occurs after a competent particle interacts with the coastline i.e. after age>min_settlement_age_seconds
# Benthic settlement occurs when a competent particle makes contact with the bottom i.e. after age>min_settlement_age_seconds
# Habitat type is not considered in either coastal or benthic settlement
# 
# The model overload the seabed and coastline interaction subfunction from basemodel.py :
#    > lift_elements_to_seafloor
#    > interact_with_coastline
# 
#  Authors : Craig Norrie, Simon Weppe
# 
# 
#  Under development - more testing to do
# 
# 


import numpy as np
from opendrift.models.oceandrift import OceanDrift, Lagrangian3DArray
import logging; logger = logging.getLogger(__name__)


# Defining the  element properties from Pelagicegg model
class BivalveLarvaeObj(Lagrangian3DArray):
    """Extending Lagrangian3DArray with specific properties for pelagic eggs/larvae
    """

    variables = Lagrangian3DArray.add_variables([
        ('diameter', {'dtype': np.float32,
                      'units': 'm',
                      'default': 0.0014}),  # for NEA Cod
        ('neutral_buoyancy_salinity', {'dtype': np.float32,
                                       'units': '[]',
                                       'default': 31.25}),  # for NEA Cod
        ('density', {'dtype': np.float32,
                     'units': 'kg/m^3',
                     'default': 1028.}),
        ('hatched', {'dtype': np.float32,
                     'units': '',
                     'default': 0.}),
        ('terminal_velocity', {'dtype': np.float32,
                       'units': 'm/s',
                       'default': 0.})])


class BivalveLarvae(OceanDrift):
    """Buoyant particle trajectory model based on the OpenDrift framework.

        Developed at MET Norway

        Generic module for particles that are subject to vertical turbulent
        mixing with the possibility for positive or negative buoyancy

        Particles could be e.g. oil droplets, plankton, or sediments

        Under construction.
    """

    ElementType = BivalveLarvaeObj
    # ElementType = BuoyantTracer 

    required_variables = {
        'x_sea_water_velocity': {'fallback': 0},
        'y_sea_water_velocity': {'fallback': 0},
        'sea_surface_wave_significant_height': {'fallback': 0},
        'sea_ice_area_fraction': {'fallback': 0},
        'x_wind': {'fallback': 0},
        'y_wind': {'fallback': 0},
        'land_binary_mask': {'fallback': None},
        'sea_floor_depth_below_sea_level': {'fallback': 100},
        'ocean_vertical_diffusivity': {'fallback': 0.02, 'profiles': True},
        'sea_water_temperature': {'fallback': 15, 'profiles': True},
        'sea_water_salinity': {'fallback': 34, 'profiles': True},
        'sea_surface_height': {'fallback': 0.0},
        'surface_downward_x_stress': {'fallback': 0},
        'surface_downward_y_stress': {'fallback': 0},
        'turbulent_kinetic_energy': {'fallback': 0},
        'turbulent_generic_length_scale': {'fallback': 0},
        'upward_sea_water_velocity': {'fallback': 0},
      }

    # Vertical profiles of the following parameters will be available in
    # dictionary self.environment.vertical_profiles
    # E.g. self.environment_profiles['x_sea_water_velocity']
    # will be an array of size [vertical_levels, num_elements]
    # The vertical levels are available as
    # self.environment_profiles['z'] or
    # self.environment_profiles['sigma'] (not yet implemented)

    # required_profiles = ['sea_water_temperature',
    #                      'sea_water_salinity',
    #                      'ocean_vertical_diffusivity']

    # removing salt/water temp profile requirement for now
    # > need to get correct profiles from SCHISM reader

    # required_profiles = ['ocean_vertical_diffusivity']

    # The depth range (in m) which profiles shall cover
    required_profiles_z_range = [-120, 0]

    # Default colors for plotting
    status_colors = {'initial': 'green', 'active': 'blue',
                     'settled_on_coast': 'red', 'died': 'yellow', 'settled_on_bottom': 'magenta'}

    def __init__(self, *args, **kwargs):
        
        # Calling general constructor of parent class
        super(BivalveLarvae, self).__init__(*args, **kwargs)

        # By default, larvae do not strand when reaching shoreline. 
        # They are recirculated back to previous position instead
        self._set_config_default('general:coastline_action', 'previous')
        # resuspend larvae that reach seabed by default 
        self._set_config_default('general:seafloor_action', 'lift_to_seafloor') 

        # set the defasult min_settlement_age_seconds to 0.0
        # self.set_config('drift:min_settlement_age_seconds', '0.0')

        ##add config spec
        self._add_config({ 'drift:min_settlement_age_seconds': {'type': 'float', 'default': 0.0,'min': 0.0, 'max': 1.0e10, 'units': 'seconds',
                           'description': 'minimum age in seconds at which larvae can start to settle on seabed or stick to shoreline)',
                           'level': self.CONFIG_LEVEL_BASIC}})

 
    def update(self):
        """Update positions and properties of buoyant particles."""

        # Update element age
        # self.elements.age_seconds += self.time_step.total_seconds()
        # already taken care of in increase_age_and_retire() in basemodel.py

        # Horizontal advection
        self.advect_ocean_current()

        # Turbulent Mixing or settling-only 
        if self.get_config('drift:vertical_mixing') is True:
            self.update_terminal_velocity()  #compute vertical velocities, two cases possible - constant, or same as pelagic egg
            self.vertical_mixing()
        else:  # Buoyancy
            self.update_terminal_velocity()
            self.vertical_buoyancy()

        self.vertical_advection()     


    def interact_with_seafloor(self):
        """Seafloor interaction according to configuration setting"""
        # 
        # This function will overloads the version in basemodel.py
        if self.num_elements_active() == 0:
            return
        if 'sea_floor_depth_below_sea_level' not in self.priority_list:
            return
        sea_floor_depth = self.sea_floor_depth()
        below = np.where(self.elements.z < -sea_floor_depth)[0]
        if len(below) == 0:
                logger.debug('No elements hit seafloor.')
                return

        below_and_older = np.logical_and(self.elements.z < -sea_floor_depth, 
            self.elements.age_seconds >= self.get_config('drift:min_settlement_age_seconds'))
        below_and_younger = np.logical_and(self.elements.z < -sea_floor_depth, 
            self.elements.age_seconds < self.get_config('drift:min_settlement_age_seconds'))
        
        # Move all elements younger back to seafloor 
        # (could rather be moved back to previous if relevant? )
        self.elements.z[np.where(below_and_younger)] = -sea_floor_depth[np.where(below_and_younger)]

        # deactivate elements that were both below and older
        self.deactivate_elements(below_and_older ,reason='settled_on_bottom')

        logger.debug('%s elements hit seafloor, %s were older than %s sec. and deactivated, %s were lifted back to seafloor' \
            % (len(below),len(below_and_older),self.get_config('drift:min_settlement_age_seconds'),len(below_and_younger)))    

    
        # original code 
        # 
        # i = self.get_config('general:seafloor_action')
        # if i == 'lift_to_seafloor': # always the case
        #     self.elements.z[below] = -sea_floor_depth[below]
        # elif i == 'deactivate':
        #     self.deactivate_elements(self.elements.z < -sea_floor_depth, reason='seafloor')
        # elif i == 'previous':  # Go back to previous position (in water)
        #     logger.warning('%s elements hit seafloor, '
        #                    'moving back ' % len(below))
        #     below_ID = self.elements.ID[below]
        #     self.elements.lon[below] = \
        #         np.copy(self.previous_lon[below_ID - 1])
        #     self.elements.lat[below] = \
        #         np.copy(self.previous_lat[below_ID - 1])

    def update_terminal_velocity(self, Tprofiles=None, Sprofiles=None,
                                 z_index=None):
        """Calculate terminal velocity due to bouyancy from own properties
        and environmental variables. Sub-modules should overload
        this method for particle-specific behaviour
        """
        # import pdb;pdb.set_trace()
        pass

        
    def sea_surface_height(self):
        '''fetches sea surface height for presently active elements

           sea_surface_height > 0 above mean sea level
           sea_surface_height < 0 below mean sea level
        '''
        if hasattr(self, 'environment') and \
                hasattr(self.environment, 'sea_surface_height'):
            if len(self.environment.sea_surface_height) == \
                    self.num_elements_active():
                sea_surface_height = \
                    self.environment.sea_surface_height
        if 'sea_surface_height' not in locals():
            env, env_profiles, missing = \
                self.get_environment(['sea_surface_height'],
                                     time=self.time, lon=self.elements.lon,
                                     lat=self.elements.lat,
                                     z=0*self.elements.lon, profiles=None)
            sea_surface_height = \
                env['sea_surface_height'].astype('float32') 
        return sea_surface_height  


    def surface_stick(self):
        '''Keep particles just below the surface.
           (overloads the OpenDrift3DSimulation version to allow for possibly time-varying
           sea_surface_height)
        '''
        
        sea_surface_height = self.sea_surface_height() # returns surface elevation at particle positions (>0 above msl, <0 below msl)
        
        # keep particle just below sea_surface_height (self.elements.z depth are negative down)
        surface = np.where(self.elements.z >= sea_surface_height)
        if len(surface[0]) > 0:
            self.elements.z[surface] = sea_surface_height[surface] -0.01 # set particle z at 0.01m below sea_surface_height

    def interact_with_coastline(self,final = False): 
        """Coastline interaction according to configuration setting
           (overloads the interact_with_coastline() from basemodel.py)
           
           The method checks for age of particles that intersected coastlines:

             if age_particle < min_settlement_age_seconds : move larvaes back to previous wet position
             if age_particle > min_settlement_age_seconds : larvaes become stranded and will be deactivated.

        """
        i = self.get_config('general:coastline_action') # will always be 'previous'

        if not hasattr(self.environment, 'land_binary_mask'):
            return

        if final is True:  # Get land_binary_mask for final location
            en, en_prof, missing = \
                self.get_environment(['land_binary_mask'],
                                     self.time,
                                     self.elements.lon,
                                     self.elements.lat,
                                     self.elements.z,
                                     None)
            self.environment.land_binary_mask = en.land_binary_mask

        # if i == 'previous':  # Go back to previous position (in water)
        # previous_position_if = self.previous_position_if()
        if self.newly_seeded_IDs is not None:
            self.deactivate_elements(
                (self.environment.land_binary_mask == 1) &
                (self.elements.ID >= self.newly_seeded_IDs[0]),
                reason='seeded_on_land')
        on_land = np.where(self.environment.land_binary_mask == 1)[0]

            # if previous_position_if is not None:
            #     self.deactivate_elements((previous_position_if*1 == 1) & (
            #                      self.environment.land_binary_mask == 0),
            #                          reason='seeded_at_nodata_position')

        # if previous_position_if is None:
        #     on_land = np.where(self.environment.land_binary_mask == 1)[0]
        # else:
        #     on_land = np.where((self.environment.land_binary_mask == 1) |
        #                        (previous_position_if == 1))[0]
        if len(on_land) == 0:
            logger.debug('No elements hit coastline.')
        else:                
            if self.get_config('drift:min_settlement_age_seconds') == 0.0 :
                # No minimum age input, set back to previous position (same as in interact_with_coastline() from basemodel.py)
                logger.debug('%s elements hit coastline, '
                          'moving back to water' % len(on_land))
                on_land_ID = self.elements.ID[on_land]
                self.elements.lon[on_land] = \
                    np.copy(self.previous_lon[on_land_ID - 1])
                self.elements.lat[on_land] = \
                    np.copy(self.previous_lat[on_land_ID - 1])
                self.environment.land_binary_mask[on_land] = 0
            else:
                #################################
                # Minimum age before settling was input; check age of particle versus min_settlement_age_seconds
                # and strand or recirculate accordingly
                on_land_and_younger = np.where((self.environment.land_binary_mask == 1) & (self.elements.age_seconds < self.get_config('drift:min_settlement_age_seconds')))[0]
                on_land_and_older = np.where((self.environment.land_binary_mask == 1) & (self.elements.age_seconds >= self.get_config('drift:min_settlement_age_seconds')))[0]

                # this step replicates what is done is original code, but accounting for particle age. It seems necessary 
                # to have an array of ID, rather than directly indexing using the "np.where-type" index (in dint64)
                on_land_and_younger_ID = self.elements.ID[on_land_and_younger] 
                on_land_and_older_ID = self.elements.ID[on_land_and_older]

                logger.debug('%s elements hit coastline' % len(on_land))
                logger.debug('moving %s elements younger than min_settlement_age_seconds back to previous water position' % len(on_land_and_younger))
                logger.debug('%s elements older than min_settlement_age_seconds remain stranded on coast' % len(on_land_and_younger))
                
                # refloat elements younger than min_settlement_age back to previous position(s)
                if len(on_land_and_younger) > 0 :
                    # self.elements.lon[np.where(on_land_and_younger)] = np.copy(self.previous_lon[np.where(on_land_and_younger)])  
                    # self.elements.lat[np.where(on_land_and_younger)] = np.copy(self.previous_lat[np.where(on_land_and_younger)])
                    # self.environment.land_binary_mask[on_land_and_younger] = 0 

                    self.elements.lon[on_land_and_younger] = np.copy(self.previous_lon[on_land_and_younger_ID - 1])
                    self.elements.lat[on_land_and_younger] = np.copy(self.previous_lat[on_land_and_younger_ID - 1])
                    self.environment.land_binary_mask[on_land_and_younger] = 0

                # deactivate elements older than min_settlement_age & save position
                # ** function expects an array of size consistent with self.elements.lon
                self.deactivate_elements((self.environment.land_binary_mask == 1) & \
                                         (self.elements.age_seconds >= self.get_config('drift:min_settlement_age_seconds')),
                                         reason='settled_on_coast')


    def increase_age_and_retire(self):  
            # if max_age_seconds is exceeded, particle is flagged as 'died'

            """Increase age of elements, and retire if older than config setting.

               >essentially same as increase_age_and_retire() from basemodel.py, 
               only using a diffrent reason for retiring particles ('died' instead of 'retired')
               .. could probably be removed...
            """
            # Increase age of elements
            self.elements.age_seconds += self.time_step.total_seconds()

            # Deactivate elements that exceed a certain age
            if self.get_config('drift:max_age_seconds') is not None:
                self.deactivate_elements(self.elements.age_seconds >=
                                         self.get_config('drift:max_age_seconds'),
                                         reason='died')

            # Deacticate any elements outside validity domain set by user
            if self.validity_domain is not None:
                W, E, S, N = self.validity_domain
                if W is not None:
                    self.deactivate_elements(self.elements.lon < W, reason='outside')
                if E is not None:
                    self.deactivate_elements(self.elements.lon > E, reason='outside')
                if S is not None:
                    self.deactivate_elements(self.elements.lat < S, reason='outside')
                if N is not None:
                    self.deactivate_elements(self.elements.lat > N, reason='outside')


    # def lift_elements_to_seafloor(self):  
    #   # 
    #   # Initiate settlement if particles touch bottom during competence period
    #     # 
    #     '''Lift any elements which are below seafloor and check age
    #       (overloads the lift_elements_to_seafloor() from basemodel.py)

    #        The methods will check age of larvae that touched the seabed.
    #          if age_particle < min_settlement_age_seconds : resuspend larvae
    #          if age_particle > min_settlement_age_seconds : larvaes settle and will be deactivated.

    #     '''
            
    #     if 'sea_floor_depth_below_sea_level' not in self.priority_list:
    #         return
        
    #     sea_floor_depth = self.sea_floor_depth() # returns a positive down water depth
    #     sea_surface_height = self.sea_surface_height() # returns surface elevation at particle positions (>0 above msl, <0 below msl)

    #     below = self.elements.z < -sea_floor_depth
        
    #     if self.get_config('drift:lift_to_seafloor') is True: # always true
    #         # self.elements.z[below] = -sea_floor_depth[below] - intial code

    #         self.elements.z[below] = np.minimum(-sea_floor_depth[below], sea_surface_height[below])
    #         # make sure particles dont get above water at this stage i.e. z>sea_surface_height
    #         # this can happen when reader has negative values for sea_floor_depth
    #         # e.g. : sea_floor_depth() returns e.g. -2.0 (i.e. wetting-drying points)
    #         # 
    #         # if sea_surface_height is not available from reader, fallback value is used (=0 by default).

    #     # Deactivate elements that touched seabed and have age>min_settlement_age_seconds
    #     if self.get_config('drift:min_settlement_age_seconds') != 0.0:
    #         older = self.elements.age_seconds >= self.get_config('drift:min_settlement_age_seconds')
    #         if older.any():
    #             self.deactivate_elements(older & below ,reason='settled_on_bottom')