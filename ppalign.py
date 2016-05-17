#!/usr/bin/env python

#########
#ppalign#
#########

#ppalign is a command-line program used to align homogeneous data (i.e. from
#    the receiver, with the same center frequency, bandwidth, and number of
#    channels).  This is useful for making averaged portraits to either pass to
#    ppgauss.py with -M to make a Gaussian model, or to smooth and use as a
#    model with pptoas.py.

#Written by Timothy T. Pennucci (TTP; pennucci@virginia.edu).

#Need option for constant Gaussian initial guess.

import os, shlex
import subprocess as sub
from pptoas import *

def psradd_archives(metafile, outfile, palign=False):
    """
    Add together archives using psradd.

    This function will call psradd with an option to pass -P and can be used to
    make an initial guess for align_archives.

    metafile is a file containing PSRFITS archive names to be averaged.
    outfile is the name of the output archive.
    palign=True passes -P to psradd, which phase-aligns the archives, intead of
        using the ephemeris (maybe?).
    """
    psradd_cmd = "psradd "
    if palign:
        psradd_cmd += "-P "
    psradd_cmd += "-T -o %s -M %s"%(outfile, metafile)
    psradd_call = sub.Popen(shlex.split(psradd_cmd))
    psradd_call.wait()

def psrsmooth_archive(archive, options="-W"):
    """
    Smooth an archive using psrsmooth.

    This function will call psrsmooth with options to smooth an output archive
    from align_archives.

    archive is the PSRFITS archive to be smoothed.
    options are the options passed to psrsmooth.
    """
    psrsmooth_cmd = "psrsmooth " + options + " %s"%archive
    psrsmooth_call = sub.Popen(shlex.split(psrsmooth_cmd))
    psrsmooth_call.wait()

def align_archives(metafile, initial_guess, outfile=None, rot_phase=0.0,
        place=None, niter=1, quiet=False):
    """
    Iteratively align and average archives.

    Each archive is fitted for a phase, a DM, and channel amplitudes against
    initial_guess.  The average is weighted by the fitted channel amplitudes
    and channel S/N.  The average becomes the new initial alignment template
    for additional iterations.  The output archive will have a 0 DM value and
    dmc=0.

    metafile is a file containing PSRFITS archive names to be averaged.
    initial_guess is the PSRFITS archive providing the initial alignment guess.
    outfile is the name of the output archive; defaults to
        <metafile>.algnd.fits.
    rot_phase is an overall rotation to be applied to the final output archive.
    niter is the number of iterations to complete.  1-5 seems to work ok.
    quiet=True suppresses output.

    """
    datafiles = [datafile[:-1] for datafile in open(metafile, "r").readlines()]
    if outfile is None:
        outfile = metafile + ".algnd.fits"
    vap_cmd = "vap -c nchan,nbin %s"%initial_guess
    nchan,nbin = map(int, sub.Popen(shlex.split(vap_cmd), stdout=sub.PIPE
            ).stdout.readlines()[1].split()[-2:])
    model_data = load_data(initial_guess, dedisperse=True, dededisperse=False,
            tscrunch=True, pscrunch=True, fscrunch=False, rm_baseline=True,
            flux_prof=False, refresh_arch=True, return_arch=True, quiet=quiet)
    model_port = (model_data.masks * model_data.subints)[0,0]
    count = 1
    while(niter):
        print "Doing iteration %d..."%count
        nsub = 0
        load_quiet = quiet
        aligned_port = np.zeros((nchan,nbin))
        total_weights = np.zeros((nchan,nbin))
        for ifile in xrange(len(datafiles)):
            data = load_data(datafiles[ifile], dedisperse=False,
                    tscrunch=False, pscrunch=True, fscrunch=False,
                    rm_baseline=True, quiet=load_quiet)
            DM_guess = data.DM
            for isub in data.ok_isubs:
                port = data.subints[isub,0,data.ok_ichans[isub]]
                freqs = data.freqs[isub,data.ok_ichans[isub]]
                model = model_port[data.ok_ichans[isub]]
                #print freqs-model_data.freqs[0,data.ok_ichans[isub]]
                P = data.Ps[isub]
                SNRs = data.SNRs[isub,0,data.ok_ichans[isub]]
                errs = data.noise_stds[isub,0,data.ok_ichans[isub]]
                nu_fit = guess_fit_freq(freqs, SNRs)
                rot_port = rotate_data(port, 0.0, DM_guess, P, freqs,
                        nu_fit)
                phase_guess = fit_phase_shift(rot_port.mean(axis=0),
                        model.mean(axis=0)).phase
                if len(freqs) > 1:
                    results = fit_portrait(port, model,
                            np.array([phase_guess, DM_guess]), P, freqs,
                            nu_fit, None, errs, quiet=quiet)
                else:  #1-channel hack
                    results = fit_phase_shift(port[0], model[0], errs[0])
                    results.DM = data.DM
                    results.DM_err = 0.0
                    results.nu_ref = freqs[0]
                    results.nfeval = 0
                    results.return_code = -2
                    results.scales = np.array([results.scale])
                    results.scale_errs = np.array([results.scale_error])
                    results.covariance = 0.0
                weights = np.outer(results.scales / errs**2, np.ones(nbin))
                aligned_port[data.ok_ichans[isub]] += weights * \
                        rotate_data(port, results.phase, results.DM, P, freqs,
                                results.nu_ref)
                total_weights[data.ok_ichans[isub]] +=  weights
                nsub += 1
            load_quiet = True
        aligned_port[np.where(total_weights > 0)[0]] /= \
                total_weights[np.where(total_weights > 0)[0]]
        model_port = aligned_port
        niter -= 1
        count += 1
    if rot_phase:
        aligned_port = rotate_data(aligned_port, rot_phase)
    if place is not None:
        prof = aligned_port.mean(axis=0)
        delta = prof.max() * gaussian_profile(len(prof), place, 0.0001)
        phase = fit_phase_shift(prof, delta).phase
        aligned_port = rotate_data(aligned_port, phase)
    arch = model_data.arch
    arch.tscrunch()
    arch.pscrunch()
    arch.set_dispersion_measure(0.0)
    for subint in arch:
        for ipol in xrange(model_data.arch.get_npol()):
            for ichan in xrange(model_data.arch.get_nchan()):
                #subint.set_weight(ichan, weight)
                prof = subint.get_Profile(ipol, ichan)
                prof.get_amps()[:] = aligned_port[ichan]
                if total_weights[ichan].sum() == 0.0:
                    subint.set_weight(ichan, 0.0)
    arch.unload(outfile)
    if not quiet: print "\nUnloaded %s.\n"%outfile

if __name__ == "__main__":

    from optparse import OptionParser

    usage = "Usage: %prog -M <metafile> [options]"
    parser = OptionParser(usage)
    #parser.add_option("-h", "--help",
    #                  action="store_true", dest="help", default=False,
    #                  help="Show this help message and exit.")
    parser.add_option("-M", "--metafile",
                      default=None,
                      action="store", metavar="metafile", dest="metafile",
                      help="Metafile of archives to average together.")
    parser.add_option("-I", "--init",
                      default=None,
                      action="store", metavar="initial_guess",
                      dest="initial_guess",
                      help="Archive containing initial alignment guess.  psradd is used if -I is not used. [default=None]")
    parser.add_option("-o", "--outfile",
                      default=None,
                      action="store", metavar="outfile", dest="outfile",
                      help="Name of averaged output archive. [default=metafile.algnd.fits]")
    parser.add_option("-P", "--palign",
                      default=False,
                      action="store_true", dest="palign",
                      help="Passes -P to psradd if -I is not used. [default=False]")
    parser.add_option("-s", "--smooth",
                      default=False,
                      action="store_true", dest="smooth",
                      help="Smooth the output average (second output archive) with psrsmooth -W. [default=False]")
    parser.add_option("-r", "--rot",
                      default=0.0,
                      action="store", metavar="phase", dest="rot_phase",
                      help="Additional rotation to add to averaged archive. [default=0.0]")
    parser.add_option("--place",
                      default=None,
                      action="store", metavar="place", dest="place",
                      help="Roughly place pulse to be at the phase of the provided argument.  Overrides --rot. [default=None]")
    parser.add_option("--niter",
                      action="store", metavar="int", dest="niter", default=1,
                      help="Number of iterations to complete. [default=1]")
    parser.add_option("--verbose",
                      action="store_false", dest="quiet", default=True,
                      help="More to stdout.")

    (options, args) = parser.parse_args()

    if options.metafile is None or not options.niter:
        print "\nppalign.py - Aligns and averages homogeneous archives by fitting DMs and phases\n"
        parser.print_help()
        print ""
        parser.exit()

    metafile = options.metafile
    initial_guess = options.initial_guess
    outfile = options.outfile
    palign = options.palign
    smooth = options.smooth
    rot_phase = np.float64(options.rot_phase)
    place = np.float64(options.place)
    if place is not None:
        rot_phase=0.0
    niter = int(options.niter)
    quiet = options.quiet

    rm = False
    if initial_guess is None:
        tmp_file = "ppalign.tmp.fits"
        psradd_archives(metafile, outfile=tmp_file, palign=palign)
        initial_guess = tmp_file
        rm = True
    align_archives(metafile, initial_guess=initial_guess, outfile=outfile,
            rot_phase=rot_phase, place=place, niter=niter, quiet=quiet)
    if smooth:
        if outfile is None:
            outfile = metafile + ".algnd.fits"
        psrsmooth_archive(outfile, options="-W")
    if rm:
        rm_cmd = "rm -f %s"%tmp_file
        rm_call = sub.Popen(shlex.split(rm_cmd))
        rm_call.wait()
