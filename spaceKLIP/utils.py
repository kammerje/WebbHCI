from __future__ import division

import matplotlib
matplotlib.rcParams.update({'font.size': 14})


# =============================================================================
# IMPORTS
# =============================================================================

import os
import pdb
import sys

import astropy.io.fits as pyfits
import matplotlib.pyplot as plt
import numpy as np

import importlib
import scipy.ndimage.interpolation as sinterp

from scipy.integrate import simps
from scipy.ndimage import fourier_shift, gaussian_filter
from scipy.ndimage import shift as spline_shift

from webbpsf_ext.imreg_tools import get_coron_apname as nircam_apname

import logging
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


# =============================================================================
# MAIN
# =============================================================================

def read_obs(fitsfile,
             return_var=False):
    """
    Read an observation from a FITS file.
    
    Parameters
    ----------
    fitsfile : path
        Path of input FITS file.
    return_var : bool, optional
        Return VAR_POISSON and VAR_RNOISE arrays? The default is False.
    
    Returns
    -------
    data : 3D-array
        'SCI' extension data.
    erro : 3D-array
        'ERR' extension data.
    pxdq : 3D-array
        'DQ' extension data.
    head_pri : FITS header
        Primary FITS header.
    head_sci : FITS header
        'SCI' extension FITS header.
    is2d : bool
        Is the original data 2D?
    imshifts : 2D-array
        Array of shape (nints, 2) containing the total shifts applied to the
        frames. None if not available.
    maskoffs : 2D-array
        Array of shape (nints, 2) containing the offsets between the star and
        coronagraphic mask position. None if not available.
    var_poisson : 3D-array, optional
        'VAR_POISSON' extension data.
    var_rnoise : 3D-array, optional
        'VAR_RNOISE' extension data.
    
    """
    
    # Read FITS file.
    hdul = pyfits.open(fitsfile)
    data = hdul['SCI'].data
    erro = hdul['ERR'].data
    pxdq = hdul['DQ'].data
    head_pri = hdul[0].header
    head_sci = hdul['SCI'].header
    is2d = False
    if data.ndim == 2:
        data = data[np.newaxis, :]
        erro = erro[np.newaxis, :]
        pxdq = pxdq[np.newaxis, :]
        is2d = True
    if data.ndim != 3:
        raise UserWarning('Requires 2D/3D data cube')
    try:
        imshifts = hdul['IMSHIFTS'].data
    except KeyError:
        imshifts = None
    try:
        maskoffs = hdul['MASKOFFS'].data
    except KeyError:
        maskoffs = None
    if return_var:
        var_poisson = hdul['VAR_POISSON'].data
        var_rnoise = hdul['VAR_RNOISE'].data
    hdul.close()
    
    if return_var:
        return data, erro, pxdq, head_pri, head_sci, is2d, imshifts, maskoffs, var_poisson, var_rnoise
    else:
        return data, erro, pxdq, head_pri, head_sci, is2d, imshifts, maskoffs

def write_obs(fitsfile,
              output_dir,
              data,
              erro,
              pxdq,
              head_pri,
              head_sci,
              is2d,
              imshifts=None,
              maskoffs=None,
              var_poisson=None,
              var_rnoise=None):
    """
    Write an observation to a FITS file.
    
    Parameters
    ----------
    fitsfile : path
        Path of input FITS file.
    output_dir : path
        Directory where the output FITS file shall be saved.
    data : 3D-array
        'SCI' extension data.
    erro : 3D-array
        'ERR' extension data.
    pxdq : 3D-array
        'DQ' extension data.
    head_pri : FITS header
        Primary FITS header.
    head_sci : FITS header
        'SCI' extension FITS header.
    is2d : bool
        Is the original data 2D?
    imshifts : 2D-array, optional
        Array of shape (nints, 2) containing the total shifts applied to the
        frames. The default is None.
    maskoffs : 2D-array, optional
        Array of shape (nints, 2) containing the offsets between the star and
        coronagraphic mask position. The default is None.
    var_poisson : 3D-array, optional
        'VAR_POISSON' extension data. The default is None.
    var_rnoise : 3D-array, optional
        'VAR_RNOISE' extension data. The default is None.
    
    Returns
    -------
    fitsfile : path
        Path of output FITS file.
    
    """
    
    # Write FITS file.
    hdul = pyfits.open(fitsfile)
    if is2d:
        hdul['SCI'].data = data[0]
        hdul['ERR'].data = erro[0]
        hdul['DQ'].data = pxdq[0]
    else:
        hdul['SCI'].data = data
        hdul['ERR'].data = erro
        hdul['DQ'].data = pxdq
    hdul[0].header = head_pri
    hdul['SCI'].header = head_sci
    if imshifts is not None:
        try:
            hdul['IMSHIFTS'].data = imshifts
        except KeyError:
            hdu = pyfits.ImageHDU(imshifts, name='IMSHIFTS')
            hdul.append(hdu)
    if maskoffs is not None:
        try:
            hdul['MASKOFFS'].data = maskoffs
        except KeyError:
            hdu = pyfits.ImageHDU(maskoffs, name='MASKOFFS')
            hdul.append(hdu)
    if var_poisson is not None:
        hdul['VAR_POISSON'].data = var_poisson
    if var_rnoise is not None:
        hdul['VAR_RNOISE'].data = var_rnoise
    fitsfile = os.path.join(output_dir, os.path.split(fitsfile)[1])
    hdul.writeto(fitsfile, output_verify='fix', overwrite=True)
    hdul.close()
    
    return fitsfile

def read_msk(maskfile):
    """
    Read a PSF mask from a FITS file.
    
    Parameters
    ----------
    maskfile : path
        Path of input FITS file.
    
    Returns
    -------
    mask : 2D-array
        PSF mask. None if not available.
    
    """
    
    # Read FITS file.
    if maskfile != 'NONE':
        hdul = pyfits.open(maskfile)
        mask = hdul['SCI'].data
        hdul.close()
    else:
        mask = None
    
    return mask


def write_msk(maskfile,
              mask,
              fitsfile):
    """
    Write a PSF mask to a FITS file.
    
    Parameters
    ----------
    maskfile : path
        Path of input FITS file.
    mask : 2D-array
        PSF mask. None if not available.
    fitsfile : path
        Path of output FITS file (to save the PSF mask in the same directory).
    
    Returns
    -------
    maskfile : path
        Path of output FITS file.
    
    """
    
    # Write FITS file.
    if mask is not None:
        hdul = pyfits.open(maskfile)
        hdul['SCI'].data = mask
        maskfile = fitsfile.replace('.fits', '_psfmask.fits')
        hdul.writeto(maskfile, output_verify='fix', overwrite=True)
        hdul.close()
    else:
        maskfile = 'NONE'
    
    return maskfile

def read_red(fitsfile):
    """
    Read a reduction from a FITS file.
    
    Parameters
    ----------
    fitsfile : path
        Path of input FITS file.
    
    Returns
    -------
    data : 3D-array
        'SCI' extension data.
    head_pri : FITS header
        Primary FITS header.
    head_sci : FITS header
        'SCI' extension FITS header.
    is2d : bool
        Is the original data 2D?
    
    """
    
    # Read FITS file.
    hdul = pyfits.open(fitsfile)
    data = hdul[0].data
    if data is None:
        try:
            data = hdul['SCI'].data
        except:
            raise UserWarning('Could not find any data')
    head_pri = hdul[0].header
    try:
        head_sci = hdul['SCI'].header
    except:
        head_sci = None
    hdul.close()
    is2d = False
    if data.ndim == 2:
        data = data[np.newaxis, :]
        is2d = True
    if data.ndim != 3:
        raise UserWarning('Requires 2D/3D data cube')
    
    return data, head_pri, head_sci, is2d

def write_fitpsf_images(fitpsf,
                        fitsfile,
                        row):
    """
    Write a best fit FM PSF to a FITS file.
    
    Parameters
    ----------
    fitpsf : pyklip.fitpsf
        PyKLIP PSF fitting object whose best fit FM PSF shall be saved.
    fitsfile : path
        Path of output FITS file.
    row : astropy.table.Row
        Astropy table row of the companion to be saved to the FITS file.
    
    Returns
    -------
    None.
    
    """
    
    # Make best fit FM PSF.
    dx = fitpsf.fit_x.bestfit - fitpsf.data_stamp_x_center
    dy = fitpsf.fit_y.bestfit - fitpsf.data_stamp_y_center
    fm_bestfit = fitpsf.fit_flux.bestfit * sinterp.shift(fitpsf.fm_stamp, [dy, dx])
    if fitpsf.padding > 0:
        fm_bestfit = fm_bestfit[fitpsf.padding:-fitpsf.padding, fitpsf.padding:-fitpsf.padding]
    
    # Make residual image.
    residual_image = fitpsf.data_stamp - fm_bestfit
    snr = np.nanmax(fm_bestfit) / np.nanstd(residual_image)
    row['SNR'] = snr
    
    # Write FITS file.
    pri = pyfits.PrimaryHDU()
    for key in row.keys():
        if key in ['FLUX_SI', 'FLUX_SI_ERR', 'LN(Z/Z0)', 'TP_CORONMSK', 'TP_COMSUBST'] and np.isnan(row[key]):
            pri.header[key] = 'NONE'
        else:
            pri.header[key] = row[key]
    sci = pyfits.ImageHDU(fitpsf.data_stamp, name='SCI')
    mod = pyfits.ImageHDU(fm_bestfit, name='MOD')
    res = pyfits.ImageHDU(residual_image, name='RES')
    hdul = pyfits.HDUList([pri, sci, res, mod])
    hdul.writeto(fitsfile, output_verify='fix', overwrite=True)
    
    pass

def crop_image(image,
               xycen,
               npix,
               return_indices=False):
    """
    Crop an image.
    
    Parameters
    ----------
    image : 2D-array
        Input image to be cropped.
    xycen : tuple of float
        Center around which the image shall be cropped. Will be rounded.
    npix : float
        Size of the cropped image. Will be rounded.
    return_indices : bool, optional
        If True, returns the x- and y-indices of the cropped image in the
        coordinate frame of the input image. The default is False.
    
    Returns
    -------
    imsub : 2D-array
        The cropped image.
    xsub_indarr : 1D-array, optional
        The x-indices of the cropped image in the coordinate frame of the
        input image.
    ysub_indarr : 1D-array, optional
        The y-indices of the cropped image in the coordinate frame of the
        input image.
    
    """
    
    # Compute pixel coordinates.
    xc, yc = xycen
    x1 = int(xc - npix / 2. + 0.5)
    x2 = x1 + npix
    y1 = int(yc - npix / 2. + 0.5)
    y2 = y1 + npix
    
    # Crop image.
    imsub = image[y1:y2, x1:x2]
    if return_indices:
        xsub_indarr = np.arange(x1, x2).astype('int')
        ysub_indarr = np.arange(y1, y2).astype('int')
        return imsub, xsub_indarr, ysub_indarr
    else:
        return imsub

def imshift(image,
            shift,
            pad=False,
            cval=0.,
            method='fourier',
            kwargs={}):
    """
    Shift an image.
    
    Parameters
    ----------
    image : 2D-array
        Input image to be shifted.
    shift : 1D-array
        X- and y-shift to be applied.
    pad : bool, optional
        Pad the image before shifting it? Otherwise, it will wrap around
        the edges. The default is True.
    cval : float, optional
        Fill value for the padded pixels. The default is 0.
    method : 'fourier' or 'spline' (not recommended), optional
        Method for shifting the frames. The default is 'fourier'.
    kwargs : dict, optional
        Keyword arguments for the scipy.ndimage.shift routine. The default
        is {}.
    
    Returns
    -------
    imsft : 2D-array
        The shifted image.
    
    """
    
    if pad:
        
        # Pad image.
        sy, sx = image.shape
        xshift, yshift = shift
        padx = np.abs(int(xshift)) + 5
        pady = np.abs(int(yshift)) + 5
        impad = np.pad(image, ((pady, pady), (padx, padx)), mode='constant', constant_values=cval)
        
        # Shift image.
        if method == 'fourier':
            imsft = np.fft.ifftn(fourier_shift(np.fft.fftn(impad), shift[::-1])).real
        elif method == 'spline':
            imsft = spline_shift(impad, shift[::-1], **kwargs)
        else:
            raise UserWarning('Image shift method "' + method + '" is not known')
        
        # Crop image to original size.
        return imsft[pady:pady + sy, padx:padx + sx]
    else:
        if method == 'fourier':
            return np.fft.ifftn(fourier_shift(np.fft.fftn(image), shift[::-1])).real
        elif method == 'spline':
            return spline_shift(image, shift[::-1], **kwargs)
        else:
            raise UserWarning('Image shift method "' + method + '" is not known')

def alignlsq(shift,
             image,
             ref_image,
             mask=None,
             method='fourier',
             kwargs={}):
    """
    Align an image to a reference image using a Fourier shift and subtract
    method.
    
    Parameters
    ----------
    shift : 1D-array
        X- and y-shift and scaling factor to be applied.
    image : 2D-array
        Input image to be aligned to a reference image.
    ref_image : 2D-array
        Reference image.
    mask : 2D-array, optional
        Weights to be applied to the input and reference images. The
        default is None.
    method : 'fourier' or 'spline' (not recommended), optional
        Method for shifting the frames. The default is 'fourier'.
    kwargs : dict, optional
        Keyword arguments for the scipy.ndimage.shift routine. The default
        is {}.
    
    Returns
    -------
    imres : 1D-array
        Residual image collapsed into one dimension.
    
    """
    
    if mask is None:
        return (ref_image - shift[2] * imshift(image, shift[:2], method=method, kwargs=kwargs)).ravel()
    else:
        return ((ref_image - shift[2] * imshift(image, shift[:2], method=method, kwargs=kwargs)) * mask).ravel()

def recenterlsq(shift,
                image,
                method='fourier',
                kwargs={}):
    """
    Center a PSF on its nearest pixel by maximizing its peak count.
    
    Parameters
    ----------
    shift : 1D-array
        X- and y-shift to be applied.
    image : 2D-array
        Input image to be recentered.
    method : 'fourier' or 'spline' (not recommended), optional
        Method for shifting the frames. The default is 'fourier'.
    kwargs : dict, optional
        Keyword arguments for the scipy.ndimage.shift routine. The default
        is {}.
    
    Returns
    -------
    invpeak : float
        Inverse of the PSF's peak count.
    
    """
    
    return 1. / np.nanmax(imshift(image, shift, method=method, kwargs=kwargs))

def subtractlsq(shift,
                image,
                ref_image,
                mask=None):
    """
    Scale and subtract a reference from a science image.
    
    Parameters
    ----------
    shift : 1D-array
        Scaling factor between the science and the reference PSF.
    image : 2D-array
        Input image to be reference PSF-subtracted.
    ref_image : 2D-array
        Reference image.
    mask : 2D-array, optional
        Mask to be applied to the input and reference images. The default is
        None.
    
    Returns
    -------
    imres : 1D-array
        Residual image collapsed into one dimension.
    
    """
    
    res = image - shift[0] * ref_image
    res = res - gaussian_filter(res, 5)
    if mask is None:
        return res.ravel()
    else:
        return res[mask]

def get_tp_comsubst(instrume,
                    subarray,
                    filt):
    """
    Get the COM substrate transmission averaged over the respective filter
    profile.
    
    Parameters
    ----------
    instrume : 'NIRCAM', 'NIRISS', or 'MIRI'
        JWST instrument in use.
    subarray : str
        JWST subarray in use.
    filt : str
        JWST filter in use.
    
    Returns
    -------
    tp_comsubst : float
        COM substrate transmission averaged over the respective filter profile
    
    """
    
    from webbpsf_ext.bandpasses import nircam_filter, nircam_com_th

    # Default return.
    tp_comsubst = 1.
    
    # If NIRCam.
    instrume = instrume.upper()
    if instrume == 'NIRCAM':
        
        # If coronagraphy subarray.
        if '210R' in subarray or '335R' in subarray or '430R' in subarray or 'SWB' in subarray or 'LWB' in subarray:
            
            # Read bandpass.
            try:
                bp = nircam_filter(filt)
                bandpass_wave = bp.wave / 1e4  # micron
                bandpass_throughput = bp.throughput
            except FileNotFoundError:
                log.error('--> Filter ' + filt + ' not found for instrument ' + instrume)
            
            # Read COM substrate transmission interpolated at bandpass wavelengths.
            comsubst_throughput = nircam_com_th(bandpass_wave)

            # Compute weighted average of COM substrate transmission.
            tp_comsubst = np.average(comsubst_throughput, weights=bandpass_throughput)
    
    # Return.
    return tp_comsubst

def get_filter_info(instrument, timeout=1, do_svo=True, return_more=False):
    """ Load filter information from the SVO Filter Profile Service or webbpsf

    Load NIRCam, NIRISS, and MIRI filters from the SVO Filter Profile Service.
    http://svo2.cab.inta-csic.es/theory/fps/

    If timeout to server, then use local copy of filter list and load through webbpsf.

    Parameters
    ----------
    instrument : str
        Name of instrument to load filter list for. 
        Must be one of 'NIRCam', 'NIRISS', or 'MIRI'.
    timeout : float
        Timeout in seconds for connection to SVO Filter Profile Service.
    do_svo : bool
        If True, try to load filter list from SVO Filter Profile Service. 
        If False, use webbpsf without first check web server.
    return_more : bool
        If True, also return `do_svo` variable, whether SVO was used or not.
    """

    from astroquery.svo_fps import SvoFps
    import webbpsf

    iname_upper = instrument.upper()

    # Try to get filter list from SVO
    if do_svo:
        try:
            filter_list = SvoFps.get_filter_list(facility='JWST', instrument=iname_upper, timeout=timeout)
        except:
            log.warning('Using SVO Filter Profile Service timed out. Using WebbPSF instead.')
            do_svo = False

    # If unsuccessful, use webbpsf to get filter list
    if not do_svo:
        inst_func = {
            'NIRCAM': webbpsf.NIRCam,
            'NIRISS': webbpsf.NIRISS,
            'MIRI'  : webbpsf.MIRI,
        }
        inst = inst_func[iname_upper]()
        filter_list = inst.filter_list

    wave, weff = ({}, {})
    if do_svo:
        for i in range(len(filter_list)):
            name = filter_list['filterID'][i]
            name = name[name.rfind('.') + 1:]
            wave[name] = filter_list['WavelengthMean'][i] / 1e4  # micron
            weff[name] = filter_list['WidthEff'][i] / 1e4  # micron
    else:
        for filt in filter_list:
            bp = inst._get_synphot_bandpass(filt)
            wave[filt] = bp.avgwave().to_value('micron')
            weff[filt] = bp.equivwidth().to_value('micron')

    if return_more:
        return wave, weff, do_svo
    else:
        return wave, weff

def expand_mask(bpmask, npix, grow_diagonal=False):
    """Expand bad pixel mask by npix pixels
    
    Parameters
    ==========
    bpmask : 2D array
        Boolean bad pixel mask
    npix : int
        Number of pixels to expand mask by
    diagonal : bool
        Expand mask by npix pixels in all directions, including diagonals
    in_place : bool
        Modify the original mask (True) or return a copy (False)

    Returns
    =======
    bpmask : 2D array of booleans
        Expanded bad pixel mask
    """
    from scipy.ndimage import binary_dilation, generate_binary_structure

    if npix==0:
        return bpmask

    # Expand mask by npix pixels, including corners
    if grow_diagonal:
        # Perform normal dilation without corners (just left, right, up, down)
        if npix>1:
            bpmask = binary_dilation(bpmask, iterations=npix-1)
        # Add corners in final iteration
        struct2 = generate_binary_structure(2, 2)
        bpmask = binary_dilation(bpmask, structure=struct2)
    else: # No corners
        bpmask = binary_dilation(bpmask, iterations=npix)

    return bpmask

def cube_fit(tarr, data, sat_vals, sat_frac=0.95, bias=None, 
             deg=1, bpmask_arr=None, fit_zero=False, verbose=False,
             use_legendre=False, lxmap=None, return_lxmap=False,
             return_chired=False):
    """Fit unsaturated data and return coefficients"""
        
    from webbpsf_ext.maths import jl_poly_fit, jl_poly

    nz, ny, nx = data.shape
    
    # Subtract bias?
    imarr = data if bias is None else data - bias
        
    # Array of masked pixels (saturated)
    mask_good = imarr < sat_frac*sat_vals
    if bpmask_arr is not None:
        mask_good = mask_good & ~bpmask_arr
    
    # Reshape for all pixels in single dimension
    imarr = imarr.reshape([nz, -1])
    mask_good = mask_good.reshape([nz, -1])

    # Initial 
    cf = np.zeros([deg+1, nx*ny])
    if return_lxmap:
        lx_min = np.zeros([nx*ny])
        lx_max = np.zeros([nx*ny])
    if return_chired:
        chired = np.zeros([nx*ny])

    # For each 
    npix_sum = 0
    i0 = 0 if fit_zero else 1
    for i in np.arange(i0,nz)[::-1]:
        ind = (cf[1] == 0) & (mask_good[i])
        npix = np.sum(ind)
        npix_sum += npix
        
        if verbose:
            print(i+1,npix,npix_sum, 'Remaining: {}'.format(nx*ny-npix_sum))
            
        if npix>0:
            if fit_zero:
                x = np.concatenate(([0], tarr[0:i+1]))
                y = np.concatenate((np.zeros([1, np.sum(ind)]), imarr[0:i+1,ind]), axis=0)
            else:
                x, y = (tarr[0:i+1], imarr[0:i+1,ind])

            if return_lxmap:
                lx_min[ind] = np.min(x) if lxmap is None else lxmap[0]
                lx_max[ind] = np.max(x) if lxmap is None else lxmap[1]
                
            # Fit line if too few points relative to polynomial degree
            if len(x) <= deg+1:
                cf[0:2,ind] = jl_poly_fit(x,y, deg=1, use_legendre=use_legendre, lxmap=lxmap)
            else:
                cf[:,ind] = jl_poly_fit(x,y, deg=deg, use_legendre=use_legendre, lxmap=lxmap)

            # Get reduced chi-sqr metric for poorly fit data
            if return_chired:
                yfit = jl_poly(x, cf[:,ind])
                deg_chi = 1 if len(x)<=deg+1 else deg
                dof = y.shape[0] - deg_chi
                chired[ind] = chisqr_red(y, yfit=yfit, dof=dof)

    imarr = imarr.reshape([nz,ny,nx])
    mask_good = mask_good.reshape([nz,ny,nx])
    
    cf = cf.reshape([deg+1,ny,nx])
    if return_lxmap:
        lxmap_arr = np.array([lx_min, lx_max]).reshape([2,ny,nx])
        if return_chired:
            chired = chired.reshape([ny,nx])
            return cf, lxmap_arr, chired
        else:
            return cf, lxmap_arr
    else:
        if return_chired:
            chired = chired.reshape([ny,nx])
            return cf, chired
        else:
            return cf
        
def chisqr_red(yvals, yfit=None, err=None, dof=None,
               err_func=np.std):
    """ Calculate reduced chi square metric
    
    If yfit is None, then yvals assumed to be residuals.
    In this case, `err` should be specified.
    
    Parameters
    ==========
    yvals : ndarray
        Sampled values.
    yfit : ndarray
        Model fit corresponding to `yvals`.
    dof : int
        Number of degrees of freedom (nvals - nparams - 1).
    err : ndarray or float
        Uncertainties associated with `yvals`. If not specified,
        then use yvals point-to-point differences to estimate
        a single value for the uncertainty.
    err_func : func
        Error function uses to estimate `err`.
    """
    
    if (yfit is None) and (err is None):
        print("Both yfit and err cannot be set to None.")
        return
    
    diff = yvals if yfit is None else yvals - yfit
    
    sh_orig = diff.shape
    ndim = len(sh_orig)
    if ndim==1:
        if err is None:
            err = err_func(yvals[1:] - yvals[0:-1]) / np.sqrt(2)
        dev = diff / err
        chi_tot = np.sum(dev**2)
        dof = len(chi_tot) if dof is None else dof
        chi_red = chi_tot / dof
        return chi_red
    
    # Convert to 2D array
    if ndim==3:
        sh_new = [sh_orig[0], -1]
        diff = diff.reshape(sh_new)
        yvals = yvals.reshape(sh_new)
        
    # Calculate errors for each element
    if err is None:
        err_arr = np.array([yvals[i+1] - yvals[i] for i in range(sh_orig[0]-1)])
        err = err_func(err_arr, axis=0) / np.sqrt(2)
        del err_arr
    else:
        err = err.reshape(diff.shape)
    # Get reduced chi sqr for each element
    dof = sh_orig[0] if dof is None else dof
    chi_red = np.sum((diff / err)**2, axis=0) / dof
    
    if ndim==3:
        chi_red = chi_red.reshape(sh_orig[-2:])
        
    return chi_red
