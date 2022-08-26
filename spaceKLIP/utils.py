from __future__ import division


# =============================================================================
# IMPORTS
# =============================================================================

import os

os.environ['CRDS_PATH'] = '../crds_cache'
os.environ['CRDS_SERVER_URL'] = 'https://jwst-crds.stsci.edu'

import astropy.io.fits as pyfits
import matplotlib.pyplot as plt
import numpy as np

import astropy.units as u

from scipy.interpolate import RegularGridInterpolator
from scipy.ndimage import fourier_shift, rotate, shift
from scipy.optimize import minimize
from synphot import Observation, SourceSpectrum, SpectralElement
from synphot.models import Empirical1D
from synphot.units import convert_flux

import pyklip.instruments.JWST as JWST
import webbpsf
import webbpsf_ext 

from jwst import datamodels
from jwst.coron import AlignRefsStep

from . import io

rad2mas = 180./np.pi*3600.*1000.


# =============================================================================
# MAIN
# =============================================================================

def fourier_imshift(image, shift):
    """
    Fourier image shift. Adapted from JWST stage 3 pipeline.
    
    Parameters
    ----------
    image : array
        A 2D/3D image to be shifted.
    shift : array
        xshift, yshift.
    
    Returns
    -------
    offset : array
        Shifted image.
    
    """
    
    if (image.ndim == 2):
        shift = np.asanyarray(shift)[:2]
        offset_image = fourier_shift(np.fft.fftn(image), shift[::-1])
        offset = np.fft.ifftn(offset_image).real
    
    elif (image.ndim == 3):
        nslices = image.shape[0]
        shift = np.asanyarray(shift)[:, :2]
        if (shift.shape[0] != nslices):
            raise ValueError('The number of provided shifts must be equal to the number of slices in the input image')
        
        offset = np.empty_like(image, dtype=float)
        for k in range(nslices):
            offset[k] = fourier_imshift(image[k], shift[k])
    
    else:
        raise ValueError('Input image must be either a 2D or a 3D array')
    
    return offset

def shift_invpeak(shift, image):
    """
    Shift an image and compute the inverse of its peak count.
    
    Parameters
    ----------
    shift : array
        xshift, yshift.
    image : array
        A 2D image to be shifted.
    
    Returns
    -------
    invpeak : float
        Inverse of the peak count of the shifted image.
    
    """
    
    # Fourier shift the image.
    offset = fourier_imshift(image, shift)
    
    # Compute the inverse of its peak count.
    invpeak = 1./np.max(offset)
    
    return invpeak

def recenter(image):
    """
    Recenter an image by shifting it around and minimizing the inverse of its
    peak count (i.e., maximizing its peak count).
    
    Parameters
    ----------
    image : array
        A 2D image to be recentered.
    
    Returns
    -------
    shift : array
        xshift, yshift.
    
    """
    
    # Find the shift that recenters the image.
    p0 = np.array([0., 0.])
    shift = minimize(shift_invpeak,
                     p0,
                     args=(image))['x']
    
    return shift

def get_offsetpsf(meta, key, recenter_offsetpsf=False, derotate=True):
    """
    Get a derotated and integration time weighted average of an offset PSF
    from WebbPSF. Try to load it from the offsetpsfdir and generate it if it
    is not in there, yet. The offset PSF will be normalized to a total
    intensity of 1.
    
    Parameters
    ----------
    meta : object of type meta
        Meta object that contains all the metadata of the observations.
    key : str
        Dictionary key of the meta.obs dictionary specifying the considered
        concatenation.
    recenter : bool
        Recenter the offset PSF? The offset PSF from WebbPSF is not properly
        centered because the wedge mirror that folds the light onto the
        coronagraphic subarrays introduces a chromatic shift.
    derotate : bool
        Derotate (and integreation time weigh) the offset PSF?
    
    Returns
    -------
    totpsf : array
        Derotated and integration time weighted average of the offset PSF.
    
    """
    
    # Try to load the offset PSF from the offsetpsfdir and generate it if it
    # is not in there, yet.
    offsetpsfdir = meta.offsetpsfdir
    inst = meta.instrume[key]
    filt = meta.filter[key]
    mask = meta.coronmsk[key]
    try:
        offsetpsf = np.load(offsetpsfdir+filt+'_'+mask+'.npy')
    except:
        gen_offsetpsf(offsetpsfdir, inst, filt, mask)
        offsetpsf = np.load(offsetpsfdir+filt+'_'+mask+'.npy')
    
    # Recenter the offset PSF.
    if (recenter_offsetpsf == True):
        shift = recenter(offsetpsf)
        offsetpsf = fourier_imshift(offsetpsf, shift)
    
    # Find the science target observations.
    ww_sci = np.where(meta.obs[key]['TYP'] == 'SCI')[0]
    
    # Derotate the offset PSF and coadd it weighted by the integration time of
    # the different rolls.
    if (derotate == True):
        totpsf = np.zeros_like(offsetpsf)
        totexp = 0. # s
        for i in range(len(ww_sci)):
            totint = meta.obs[key]['NINTS'][ww_sci[i]]*meta.obs[key]['EFFINTTM'][ww_sci[i]] # s
            totpsf += totint*rotate(offsetpsf.copy(), -meta.obs[key]['ROLL_REF'][ww_sci[i]], reshape=False, mode='constant', cval=0.)
            totexp += totint # s
        totpsf /= totexp
    else:
        totpsf = offsetpsf
    
    return totpsf

def gen_offsetpsf(offsetpsfdir, inst, filt, mask):
    """
    Generate an offset PSF using WebbPSF and save it in the offsetpsfdir. The
    offset PSF will be normalized to a total intensity of 1.
    
    Parameters
    ----------
    offsetpsfdir : str
        Directory where the offset PSF shall be saved to.
    inst : str
        JWST instrument.
    filt : str
        JWST filter.
    mask : str
        JWST coronagraphic mask.
    
    """
    
    # NIRCam.
    if (inst == 'NIRCAM'):
        nircam = webbpsf.NIRCam()
        
        # Apply the correct pupil mask, but no image mask (unocculted PSF).
        if (mask in ['MASKA210R', 'MASKA335R', 'MASKA430R']):
            nircam.pupil_mask = 'MASKRND'
        elif (mask in ['MASKALWB']):
            nircam.pupil_mask = 'MASKLWB'
        elif (mask in ['MASKASWB']):
            nircam.pupil_mask = 'MASKSWB'
        else:
            raise UserWarning('Unknown coronagraphic mask')
        nircam.image_mask = None
        webbpsf_inst = nircam
    
    # MIRI.
    elif (inst == 'MIRI'):
        miri = webbpsf.MIRI()
        webbpsf_inst = miri
    
    else:
        raise UserWarning('Unknown instrument')
    
    # Assign the correct filter and compute the offset PSF.
    webbpsf_inst.filter = filt
    hdul = webbpsf_inst.calc_psf(oversample=1, normalize='last')
    psf = hdul[0].data
    hdul.close()
    
    # Save the offset PSF.
    if (not os.path.exists(offsetpsfdir)):
        os.makedirs(offsetpsfdir)
    np.save(offsetpsfdir+filt+'_'+mask+'.npy', psf)
    
    return None

def get_transmission(meta, key, odir, derotate=False):
    """
    Get a derotated and integration time weighted average of a PSF mask and
    write it to meta.transmission.
    
    Note: assumes that the center of the PSF mask is aligned with the position
          of the host star PSF (except for the NIRCam bar masks).
    
    Parameters
    ----------
    meta : object of type meta
        Meta object that contains all the metadata of the observations.
    key : str
        Dictionary key of the meta.obs dictionary specifying the considered
        concatenation.
    odir : str
        Directory where the PSF mask plot shall be saved to.
    derotate : bool
        Derotate (and integreation time weigh) the PSF mask?
    
    Returns
    -------
    totmsk : array
        Derotated and integration time weighted average of the PSF mask.
    
    """
    
    # Find the science target observations.
    ww_sci = np.where(meta.obs[key]['TYP'] == 'SCI')[0]

    # Open the correct PSF mask. The assumption is that the center of the PSF
    # mask is aligned with the position of the host star PSF (except for the
    # NIRCam bar masks).
    psfmask = meta.psfmask[key]
    hdul = pyfits.open(psfmask)
    inst = meta.instrume[key]
    mask = meta.coronmsk[key]
    pxsc = meta.pixscale[key] # mas
    
    # NIRCam.
    if (inst == 'NIRCAM'):
        tp = hdul['SCI'].data[1:-1, 1:-1] # crop artifact at the edge
    
    # MIRI.
    elif (inst == 'MIRI'):
        tp, _ = JWST.trim_miri_data(hdul['SCI'].data[None, :, :], hdul['SCI'].data[None, :, :])
        tp = tp[0, 1:-1, 1:-2]
    
    else:
        raise UserWarning('Unknown instrument')
    hdul.close()
    
    # For the NIRCam bar masks, shift the PSF masks to their correct center.
    # Values outside of the subarray are filled with zeros (i.e., no
    # transmission).
    if (mask in ['MASKALWB', 'MASKASWB']):
        tp = shift(tp, (0., -meta.bar_offset[key]*1000./pxsc), mode='constant', cval=0.)
    
    # Derotate the PSF mask and coadd it weighted by the integration time of
    # the different rolls.
    if (derotate == True):
        totmsk = np.zeros_like(tp)
        totexp = 0. # s
        for i in range(len(ww_sci)):
            totint = meta.obs[key]['NINTS'][ww_sci[i]]*meta.obs[key]['EFFINTTM'][ww_sci[i]] # s
            totmsk += totint*rotate(tp.copy(), -meta.obs[key]['ROLL_REF'][ww_sci[i]], reshape=False, mode='constant', cval=0.)
            totexp += totint # s
        totmsk /= totexp
    else:
        totmsk = tp
    
    # Create a regular grid interpolator taking 2D pixel offset as an input
    # and returning the coronagraphic mask transmission.
    xr = np.arange(tp.shape[1]) # pix
    yr = np.arange(tp.shape[0]) # pix
    xx, yy = np.meshgrid(xr, yr) # pix
    xx = xx-(tp.shape[1]-1.)/2. # pix
    yy = yy-(tp.shape[0]-1.)/2. # pix
    rr = np.sqrt(xx**2+yy**2) # pix
    totmsk[rr > meta.owa] = np.nan
    meta.transmission = RegularGridInterpolator((xx[0, :], yy[:, 0]), totmsk)
    
    # Plot.
    plt.figure(figsize=(6.4, 4.8))
    ax = plt.gca()
    pp = ax.imshow(totmsk, origin='lower', extent=(tp.shape[1]/2., -tp.shape[1]/2., -tp.shape[0]/2., tp.shape[0]/2.), cmap='viridis', vmin=0, vmax=1)
    cc = plt.colorbar(pp, ax=ax)
    cc.set_label('Transmission', rotation=270, labelpad=20)
    if (derotate == True):
        ax.set_xlabel('$\Delta$RA [pix]')
        ax.set_ylabel('$\Delta$Dec [pix]')
    else:
        ax.set_xlabel('$\Delta$x [pix]')
        ax.set_ylabel('$\Delta$y [pix]')
    ax.set_title('Transmission')
    plt.tight_layout()
    plt.savefig(odir+key+'-transmission.pdf')
    plt.close()
    
    return totmsk

def field_dependent_correction(stamp,
                               stamp_dx,
                               stamp_dy,
                               meta):
    """
    Apply the coronagraphic mask transmission to a PSF stamp.
    
    Note: assumes that the pyKLIP PSF center is the center of the 
          coronagraphic mask transmission map.
    
    Note: uses a standard cartesian coordinate system so that North is +y and
          East is -x.
    
    Note: uses the coronagraphic mask transmission map stored in
          meta.transmission. Need to run get_transmission first!
    
    Parameters
    ----------
    stamp : array
        PSF stamp to which the coronagraphic mask transmission shall be
        applied.
    stamp_dx : array
        Array of the same shape as the PSF stamp containing the x-axis
        separation from the host star PSF center for each pixel.
    stamp_dy : array
        Array of the same shape as the PSF stamp containing the y-axis
        separation from the host star PSF center for each pixel.
    
    Returns
    -------
    stamp : array
        PSF stamp to which the coronagraphic mask transmission was applied.
    
    """
    
    # Apply coronagraphic mask transmission.
    xy = np.vstack((stamp_dy.flatten(), stamp_dx.flatten())).T
    transmission = meta.transmission(xy)
    transmission = transmission.reshape(stamp.shape)
    xh = transmission.shape[1]//2
    yh = transmission.shape[0]//2
    
    return transmission[yh, xh]*stamp

def get_stellar_magnitudes(meta):
    # First find out if a file was provided correctly
    if not os.path.isfile(meta.sdir):
        # Not a valid input
        raise ValueError('Stellar directory not recognised, please supply a valid filepath.')

    # Get the instrument
    instrument = list(meta.obs.keys())[0].split('_')[0] #Just use first filter, NIRCam / MIRI run separately

    # Check if this is a Vizier VOTable, if so, use webbpsf_ext
    if meta.sdir[-4:] == '.vot':
        # Pick an arbitrary bandpass+magnitude to normalise the initial SED. Exact choice
        # doesn't matter as will be be refitting this model using the provided data. Important
        # thing is the spectral type (user provided).
        bp_k = webbpsf_ext.bp_2mass('k')
        bp_mag = 5

        # Magnitude value is arbitrary, as we will be using the Vizier photometry to renormalise and fit the SED. 
        spec = webbpsf_ext.spectra.source_spectrum(name='Input Data + SED', sptype=meta.spt, mag_val=bp_mag, bp=bp_k, votable_file=meta.sdir)

        # Want to adjust where we fit the spectrum based on the observing filter, just roughly split between NIRCam and MIRI
        if instrument == 'NIRCAM' :
            wlim = [1,5]
        elif instrument == 'MIRI':
            wlim = [10, 20] 

        # Fit the SED to the selected data
        spec.fit_SED(x0=[1.0], wlim=wlim, use_err=False, verbose=False) #Don't use the error as it breaks thing, and don't print scaling value.
        # spec.plot_SED()
        # plt.show()

        # Want to convert the flux to photlam so that it matches the per photon throughputs?
        input_flux = u.Quantity(spec.sp_model.flux, str(spec.sp_model.fluxunits))
        photlam_flux = convert_flux(spec.sp_model.wave, input_flux, out_flux_unit='photlam')

        # Spectrum is originally from pysynphot (outdated), convert to synphot.
        SED = SourceSpectrum(Empirical1D, points=spec.sp_model.wave << u.Unit(str(spec.sp_model.waveunits)), lookup_table=photlam_flux << u.Unit('photlam'))
    # If not a VOTable, try to read it in. 
    else:
        try:
            # Open file and grab wavelength and flux arrays
            print("HERE")
            data = np.genfromtxt(meta.sdir).transpose()
            model_wave = data[0]
            model_flux = data[1]

            # Create a synphot spectrum
            SED = SourceSpectrum(Empirical1D, points=model_wave << u.Unit('micron'), lookup_table=model_flux << u.Unit('Jy'))
            print("HERE2")
        except:
            raise ValueError("Unable to read in provided file. Ensure format is in two columns with wavelength (microns), flux (Jy)")

    ### Now, perform synthetic observations on the SED to get stellar magnitudes
    # Get the filters used from the input datasets
    filters = [i.split('_')[2] for i in list(meta.obs.keys())]
    if ('F335M' not in filters):
        filters += ['F335M'] # make sure that TA filter is present
    
    # Calculate magnitude in each filter
    mstar = {}
    for filt in filters:
        # Read in the bandpass correctly
        bpstring = '/../resources/PCEs/{}/{}.txt'.format(instrument, filt)
        bpfile = os.path.join(os.path.dirname(__file__) + bpstring)
        
        with open(bpfile) as bandpass_file:
            bandpass_data = np.genfromtxt(bandpass_file).transpose()
            bandpass_wave = bandpass_data[0] * 1e4 #Convert from microns to angstrom
            bandpass_throughput = bandpass_data[1]

        # Create the bandpass object
        Bandpass = SpectralElement(Empirical1D, points=bandpass_wave, lookup_table=bandpass_throughput)

        # Perform synthetic observation
        Obs = Observation(SED, Bandpass, binset=Bandpass.waveset)
        VegaSED = SourceSpectrum.from_vega()
        magnitude = Obs.effstim(flux_unit='vegamag', vegaspec=VegaSED).value

        # Add magnitude to dictionary
        mstar[filt.upper()] = magnitude

    return mstar

def get_maxnumbasis(meta):
    """
    Find the maximum numbasis based on the number of available calibrator
    frames.
    
    Parameters
    ----------
    meta : object of type meta
        Meta object that contains all the metadata of the observations.
    
    Returns
    -------
    meta : object of type meta
        Meta object that contains all the metadata of the observations.
    
    """
    
    # Find the maximum numbasis based on the number of available calibrator
    # frames.
    meta.maxnumbasis = {}
    for key in meta.obs.keys():
        ww = meta.obs[key]['TYP'] == 'CAL'
        meta.maxnumbasis[key] = np.sum(meta.obs[key]['NINTS'][ww], dtype=int)
    
    return meta

def get_psfmasknames(meta):
    """
    Get the correct PSF mask for each concatenation using functionalities of
    the JWST pipeline.
    
    Parameters
    ----------
    meta : object of type meta
        Meta object that contains all the metadata of the observations.
    
    Returns
    -------
    meta : object of type meta
        Meta object that contains all the metadata of the observations.
    
    """
    
    # Create an instance of the reference star alignment JWST pipeline step.
    # This just serves as a dummy from which the get_reference_file function
    # can be used to obtain any reference file type from the online CRDS
    # database.
    step = AlignRefsStep()
    
    # Get the correct PSF mask for each concatenation.
    meta.psfmask = {}
    for key in meta.obs.keys():
        model = datamodels.open(meta.obs[key]['FITSFILE'][0])
        meta.psfmask[key] = step.get_reference_file(model, 'psfmask')
    del step
    
    return meta

def get_bar_offset(meta):
    """
    Get the correct bar offset for each concatenation from the meta object
    which contains the pySIAF bar offsets for the different NIRCam bar mask
    fiducial points in meta.offset_lwb and meta.offset_swb.
    
    Parameters
    ----------
    meta : object of type meta
        Meta object that contains all the metadata of the observations.
    
    Returns
    -------
    meta : object of type meta
        Meta object that contains all the metadata of the observations.
    
    """
    
    # Get the correct bar offset for each concatenation.
    meta.bar_offset = {}
    for key in meta.obs.keys():
        if (meta.instrume[key] == 'NIRCAM'):
            if ('LWB' in meta.coronmsk[key]):
                if ('NARROW' in meta.apername[key]):
                    meta.bar_offset[key] = meta.offset_lwb['narrow']
                else:
                    meta.bar_offset[key] = meta.offset_lwb[meta.filter[key]]
            elif ('SWB' in meta.coronmsk[key]):
                if ('NARROW' in meta.apername[key]):
                    meta.bar_offset[key] = meta.offset_swb['narrow']
                else:
                    meta.bar_offset[key] = meta.offset_swb[meta.filter[key]]
            else: # round masks
                meta.bar_offset[key] = None
        else:
            meta.bar_offset[key] = None
    
    return meta

def prepare_meta(meta, fitsfiles):
    """
    Find and write the metadata for the provided FITS files into the meta
    object. This function overwrites any metadata that was previously stored
    in the meta object.
    
    Parameters
    ----------
    meta : object of type meta
        Meta object that contains all the metadata of the observations.
    fitsfiles : list of str
        List of the FITS files whose metadata shall be extracted.
    
    Returns
    -------
    meta : object of type meta
        Meta object that contains all the metadata of the observations.
    
    """
    
    # Extract the metadata of the observations from the FITS files.
    meta = io.extract_obs(meta, fitsfiles)
    
    # Find the maximum numbasis based on the number of available calibrator
    # frames.
    meta = get_maxnumbasis(meta)
    
    # Find the names of the PSF masks from CRDS.
    meta = get_psfmasknames(meta)
    
    # Get the bar offsets for NIRCam from pySIAF.
    meta = get_bar_offset(meta)
    
    # Compute the host star magnitude in the observed filters.
    meta.mstar = get_stellar_magnitudes(meta)
    
    return meta

def azimuthalAverage(image, center=None, stddev=False, returnradii=False, return_nr=False, 
        binsize=0.5, weights=None, steps=False, interpnan=False, left=None, right=None, return_max=False):
    """
    Calculate the azimuthally averaged radial profile.
    NB: This was found online and should be properly credited! Modified by MJI

    image - The 2D image
    center - The [x,y] pixel coordinates used as the center. The default is 
             None, which then uses the center of the image (including 
             fractional pixels).
    stddev - if specified, return the azimuthal standard deviation instead of the average
    returnradii - if specified, return (radii_array,radial_profile)
    return_nr   - if specified, return number of pixels per radius *and* radius
    binsize - size of the averaging bin.  Can lead to strange results if
        non-binsize factors are used to specify the center and the binsize is
        too large
    weights - can do a weighted average instead of a simple average if this keyword parameter
        is set.  weights.shape must = image.shape.  weighted stddev is undefined, so don't
        set weights and stddev.
    steps - if specified, will return a double-length bin array and radial
        profile so you can plot a step-form radial profile (which more accurately
        represents what's going on)
    interpnan - Interpolate over NAN values, i.e. bins where there is no data?
        left,right - passed to interpnan; they set the extrapolated values
    return_max - (MJI) Return the maximum index.

    If a bin contains NO DATA, it will have a NAN value because of the
    divide-by-sum-of-weights component.  I think this is a useful way to denote
    lack of data, but users let me know if an alternative is prefered...
    
    """
    # Calculate the indices from the image
    y, x = np.indices(image.shape)

    if center is None:
        center = np.array([(x.max()-x.min())/2.0, (y.max()-y.min())/2.0])

    r = np.hypot(x - center[0], y - center[1])

    if weights is None:
        weights = np.ones(image.shape)
    elif stddev:
        raise ValueError("Weighted standard deviation is not defined.")

    # the 'bins' as initially defined are lower/upper bounds for each bin
    # so that values will be in [lower,upper)  
    nbins = int(np.round(r.max() / binsize)+1)
    maxbin = nbins * binsize
    bins = np.linspace(0,maxbin,nbins+1)
    # but we're probably more interested in the bin centers than their left or right sides...
    bin_centers = (bins[1:]+bins[:-1])/2.0

    # Find out which radial bin each point in the map belongs to
    whichbin = np.digitize(r.flat,bins)

    # how many per bin (i.e., histogram)?
    # there are never any in bin 0, because the lowest index returned by digitize is 1
    nr = np.bincount(whichbin)[1:]

    # recall that bins are from 1 to nbins (which is expressed in array terms by arange(nbins)+1 or xrange(1,nbins+1) )
    # radial_prof.shape = bin_centers.shape

    if stddev:
        radial_prof = np.array([image.flat[whichbin==b].std() for b in range(1,nbins+1)])
    elif return_max:
        radial_prof = np.array([np.append((image*weights).flat[whichbin==b],-np.inf).max() for b in range(1,nbins+1)])
    else:
        radial_prof = np.array([(image*weights).flat[whichbin==b].sum() / weights.flat[whichbin==b].sum() for b in range(1,nbins+1)])

    if interpnan:
        radial_prof = np.interp(bin_centers,bin_centers[radial_prof==radial_prof],radial_prof[radial_prof==radial_prof],left=left,right=right)

    if steps:
        xarr = np.array(zip(bins[:-1],bins[1:])).ravel() 
        yarr = np.array(zip(radial_prof,radial_prof)).ravel() 
        return xarr,yarr
    elif returnradii: 
        return bin_centers,radial_prof
    elif return_nr:
        return nr,bin_centers,radial_prof
    else:
        return radial_prof
