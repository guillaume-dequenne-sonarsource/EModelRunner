"""Creates .hoc from cell."""

# pylint: disable=too-many-arguments
import os
from datetime import datetime

import jinja2

import bluepyopt
from bluepyopt.ephys.create_hoc import (
    _generate_parameters,
    _generate_channels_by_location,
    _generate_reinitrng,
)


class HocStimuliCreator:
    """Class to create the stimuli in hoc.

    Attributes:
        apical_point_isec (int): section index of the apical point
            Set to -1 if there is no apical point
        n_stims (int): total number of protocols to be run by hoc.
            Gets incremented during initiation to enumerate the protocols.
        max_steps (int): A StepProtocol can have multiple steps. This attribute
            counts the maximum steps that the StepProtocol with the most steps has.
            Stays at 0 if there is no StepProtocol.
        reset_step_stimuli (str): hoc script resetting the step stimuli objects
            to be put inhoc file.
        init_step_stimuli (str): hoc script initiating the step stimuli objects
            to be put in hoc file.
        stims_hoc (str): hoc script containing all the protocols to be run by hoc.
            The Protocols supported by this library to be converted to hoc are:
            StepProtocol
            RampProtocol
            Vecstim
            Netstim
        save_recs (str): hoc scipt to save the recordings of each protocol.
        extra_recs_vars (str): names of the extra recordings hoc variables
            Have the form ', var1, var2, ..., var_n' to be added to the hoc variable declaration
        extra_recs (str): hoc script to declare the extra recordings
    """

    def __init__(self, prot_definitions, mtype, add_synapses, apical_point_isec):
        """Get stimuli in hoc to put in createsimulation.hoc."""
        self.apical_point_isec = apical_point_isec
        self.n_stims = 0
        self.max_steps = 0
        self.reset_step_stimuli = ""
        self.init_step_stimuli = ""
        self.stims_hoc = ""
        self.save_recs = ""
        self.extra_recs_vars = ""
        self.extra_recs = ""
        for prot_name, prot in prot_definitions.items():
            if "extra_recordings" in prot:
                self.add_extra_recs(prot["extra_recordings"])

            if "type" in prot and prot["type"] == "StepProtocol":
                self.n_stims += 1
                step_hoc = self.get_stim_hoc(self.get_step_hoc, prot)
                self.stims_hoc += step_hoc

                self.add_save_recordings_hoc(mtype, prot_name, prot)
            elif "type" in prot and prot["type"] == "RampProtocol":
                self.n_stims += 1
                ramp_hoc = self.get_stim_hoc(self.get_ramp_hoc, prot)
                self.stims_hoc += ramp_hoc

                self.add_save_recordings_hoc(mtype, prot_name, prot)
            elif "type" in prot and prot["type"] == "Vecstim" and add_synapses:
                self.n_stims += 1
                vecstim_hoc = self.get_stim_hoc(self.get_vecstim_hoc, prot)
                self.stims_hoc += vecstim_hoc

                self.add_save_recordings_hoc(mtype, prot_name, prot)
            elif "type" in prot and prot["type"] == "Netstim" and add_synapses:
                self.n_stims += 1
                netstim_hoc = self.get_stim_hoc(self.get_netstim_hoc, prot)
                self.stims_hoc += netstim_hoc

                self.add_save_recordings_hoc(mtype, prot_name, prot)

        self.get_reset_step()
        self.get_init_step()

    def add_extra_recs(self, extra_recs):
        """Add extra recordings to the recordings settings."""
        for extra_rec in extra_recs:
            name = extra_rec["name"]
            seclist_name = extra_rec["seclist_name"]
            var = extra_rec["var"]

            if name not in self.extra_recs_vars.split(", "):
                self.extra_recs_vars += f", {name}"
                if extra_rec["type"] == "nrnseclistcomp":
                    sec_index = extra_rec["sec_index"]
                    comp_x = extra_rec["comp_x"]

                    self.extra_recs += f"""
                        {name} = new Vector()
                        cell.{seclist_name}[{sec_index}] {name}.record(&{var}({comp_x}), 0.1)
                    """
                elif extra_rec["type"] == "somadistance":
                    somadistance = extra_rec["somadistance"]

                    self.extra_recs += f"""
                        {name} = new Vector()
                        secref = find_isec_at_soma_distance(cell.{seclist_name}, {somadistance})
                        comp_x = find_comp_x_at_soma_distance(secref, {somadistance})

                        secref.sec {name}.record(&{var}(comp_x), 0.1)
                    """

                elif extra_rec["type"] == "somadistanceapic":
                    somadistance = extra_rec["somadistance"]

                    self.extra_recs += f"""
                        {name} = new Vector()
                        apical_branch = get_apical_branch({self.apical_point_isec})
                        secref = find_isec_at_soma_distance(apical_branch, {somadistance})
                        comp_x = find_comp_x_at_soma_distance(secref, {somadistance})

                        secref.sec {name}.record(&{var}(comp_x), 0.1)
                    """

    def add_save_recordings_hoc(self, mtype, prot_name, prot):
        """Add this to the hoc file to save the recordings."""
        self.save_recs += f"""
            if (stim_number == {self.n_stims}) {{
                sprint(fpath.s, "hoc_recordings/{mtype}.{prot_name}.soma.v.dat")
                timevoltage = new Matrix(time.size(), 2)
                timevoltage.setcol(0, time)
                timevoltage.setcol(1, voltage)
                write_output_file(fpath, timevoltage)
        """
        if "extra_recordings" in prot:
            for extra_rec in prot["extra_recordings"]:
                var = extra_rec["var"]
                name = extra_rec["name"]
                self.save_recs += f"""
                    sprint(fpath.s, "hoc_recordings/{mtype}.{prot_name}.{name}.{var}.dat")
                    timevoltage = new Matrix(time.size(), 2)
                    timevoltage.setcol(0, time)
                    timevoltage.setcol(1, {name})
                    write_output_file(fpath, timevoltage)
                """
        self.save_recs += """
            }
        """

    def get_step_hoc(self, prot):
        """Get step stimuli in hoc format from step protocol dict."""
        step_hoc = ""

        if "holding" in prot["stimuli"]:
            hold = prot["stimuli"]["holding"]
            step_hoc += f"""
                holding_stimulus = new IClamp(0.5)
                holding_stimulus.dur = {hold["duration"]}
                holding_stimulus.del = {hold["delay"]}
                holding_stimulus.amp = {hold["amp"]}

                cell.soma holding_stimulus
            """

        step_definitions = prot["stimuli"]["step"]
        if isinstance(step_definitions, dict):
            step_definitions = [step_definitions]
        for i, step in enumerate(step_definitions):
            if i + 1 > self.max_steps:
                self.max_steps = i + 1
            step_hoc += f"""
                step_stimulus_{i} = new IClamp(0.5)
                step_stimulus_{i}.dur = {step["duration"]}
                step_stimulus_{i}.del = {step["delay"]}
                step_stimulus_{i}.amp = {step["amp"]}

                cell.soma step_stimulus_{i}
            """

        step_hoc += f"tstop={step_definitions[0]['totduration']}"

        return step_hoc

    @staticmethod
    def get_ramp_hoc(prot):
        """Get ramp stimuli in hoc format from step protocol dict."""
        ramp_hoc = ""

        if "holding" in prot["stimuli"]:
            hold = prot["stimuli"]["holding"]
            ramp_hoc += f"""
                holding_stimulus = new IClamp(0.5)
                holding_stimulus.dur = {hold["duration"]}
                holding_stimulus.del = {hold["delay"]}
                holding_stimulus.amp = {hold["amp"]}

                cell.soma holding_stimulus
            """

        ramp_definition = prot["stimuli"]["ramp"]
        # create time and amplitude of stimulus vectors
        ramp_hoc += """
            ramp_times = new Vector()
            ramp_amps = new Vector()

            ramp_times.append(0.0)
            ramp_amps.append(0.0)

            ramp_times.append({delay})
            ramp_amps.append(0.0)

            ramp_times.append({delay})
            ramp_amps.append({amplitude_start})

            ramp_times.append({delay} + {duration})
            ramp_amps.append({amplitude_end})

            ramp_times.append({delay} + {duration})
            ramp_amps.append(0.0)

            ramp_times.append({total_duration})
            ramp_amps.append(0.0)
        """.format(
            delay=ramp_definition["ramp_delay"],
            amplitude_start=ramp_definition["ramp_amplitude_start"],
            duration=ramp_definition["ramp_duration"],
            amplitude_end=ramp_definition["ramp_amplitude_end"],
            total_duration=ramp_definition["totduration"],
        )
        ramp_hoc += f"""
            ramp_stimulus = new IClamp(0.5)
            ramp_stimulus.dur = {ramp_definition["totduration"]}

            ramp_amps.play(&ramp_stimulus.amp, ramp_times, 1)

            cell.soma ramp_stimulus
        """

        ramp_hoc += f"tstop={ramp_definition['totduration']}"

        return ramp_hoc

    @staticmethod
    def get_vecstim_hoc(prot):
        """Get vecstim stimulus in hoc format from vecstim protocol dict."""
        stim = prot["stimuli"]

        vecstim_hoc = f"tstop={stim['syn_stop']}\n"

        hoc_synapse_creation = (
            "cell.synapses.create_netcons "
            + "({mode},{t0},{tf},{itv},{n_spike},{noise},{seed})"
        )
        vecstim_hoc += hoc_synapse_creation.format(
            mode=0,
            t0=stim["syn_start"],
            tf=stim["syn_stop"],
            itv=0,
            n_spike=0,
            noise=0,
            seed=stim["syn_stim_seed"],
        )

        return vecstim_hoc

    @staticmethod
    def get_netstim_hoc(prot):
        """Get netstim stimulus in hoc format from netstim protocol dict."""
        stim = prot["stimuli"]

        netstim_hoc = f"tstop={stim['syn_stop']}\n"

        hoc_synapse_creation = (
            "cell.synapses.create_netcons"
            + "({mode},{t0},{tf},{itv},{n_spike},{noise},{seed})"
        )
        netstim_hoc += hoc_synapse_creation.format(
            mode=1,
            t0=stim["syn_start"],
            tf=stim["syn_stop"],
            itv=stim["syn_interval"],
            n_spike=stim["syn_nmb_of_spikes"],
            noise=stim["syn_noise"],
            seed=0,
        )

        return netstim_hoc

    def get_stim_hoc(self, fct, prot):
        """Get stimulus in hoc."""
        stim_hoc = f"""
            if (stim_number == {self.n_stims}) {{
        """

        stim_hoc += fct(prot)

        stim_hoc += """
            }
        """
        return stim_hoc

    def get_reset_step(self):
        """Hoc script reseting all step stimuli needed by all the step protocols."""
        for i in range(max(self.max_steps, 1)):
            self.reset_step_stimuli += """
                step_stimulus_{i} = new IClamp(0.5)
                step_stimulus_{i}.dur = 0.0
                step_stimulus_{i}.del = 0.0
                step_stimulus_{i}.amp = 0.0

                cell.soma step_stimulus_{i}
            """.format(
                i=i
            )

    def get_init_step(self):
        """Hoc script initiating all step stimuli needed by all the step protocols."""
        for i in range(max(self.max_steps, 1)):
            self.init_step_stimuli += f"""
                objref step_stimulus_{i}
            """


def create_run_hoc(template_dir, template_filename, n_stims):
    """Returns a string containing run.hoc."""
    # load template
    template_path = os.path.join(template_dir, template_filename)
    with open(template_path, "r", encoding="utf-8") as template_file:
        template = template_file.read()
        template = jinja2.Template(template)

    # edit template
    return template.render(
        n_stims=n_stims,
    )


def create_synapse_hoc(
    syn_mech_args,
    syn_hoc_dir,
    template_dir,
    template_filename,
    gid,
    dt,
    synapses_template_name="synapses",
):
    """Returns a string containing the synapse hoc."""
    # load template
    template_path = os.path.join(template_dir, template_filename)
    with open(template_path, "r", encoding="utf-8") as template_file:
        template = template_file.read()
        template = jinja2.Template(template)

    # edit template
    return template.render(
        TEMPLATENAME=synapses_template_name,
        GID=gid,
        SEED=syn_mech_args["seed"],
        rng_settings_mode=syn_mech_args["rng_settings_mode"],
        syn_dir=syn_hoc_dir,
        syn_conf_file=syn_mech_args["syn_conf_file"],
        syn_data_file=syn_mech_args["syn_data_file"],
        dt=dt,
    )


def create_hoc(
    mechs,
    parameters,
    ignored_globals=(),
    replace_axon=None,
    template_name="CCell",
    template_filename="cell_template.jinja2",
    disable_banner=None,
    template_dir=None,
    add_synapses=False,
    synapses_template_name="hoc_synapses",
    syn_hoc_filename="synapses.hoc",
    syn_dir="synapses",
):
    """Return a string containing the hoc template.

    Args:
        mechs (): All the mechs for the hoc template
        parameters (): All the parameters in the hoc template
        ignored_globals (iterable str): HOC coded is added for each
        NrnGlobalParameter
        that exists, to test that it matches the values set in the parameters.
        This iterable contains parameter names that aren't checked
        replace_axon (str): String replacement for the 'replace_axon' command.
        Must include 'proc replace_axon(){ ... }
        template_name (str): name of cell class in hoc
        template_filename (str): file name of the jinja2 template
        template_dir (str): dir name of the jinja2 template
        disable_banner (bool): if not True: a banner is added to the hoc file
        add_synapses (bool): if True: synapses are loaded in the hoc
        synapses_template_name (str): synapse class name in hoc
        syn_hoc_filename (str): file name of synapse hoc file
        syn_dir (str): directory where the synapse data /files are
    """
    # pylint: disable=too-many-locals
    if template_dir is None:
        template_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "templates")
        )

    template_path = os.path.join(template_dir, template_filename)
    with open(template_path, "r", encoding="utf-8") as template_file:
        template = template_file.read()
        template = jinja2.Template(template)

    global_params, section_params, range_params, location_order = _generate_parameters(
        parameters
    )
    channels = _generate_channels_by_location(mechs, location_order)

    ignored_global_params = {}
    for ignored_global in ignored_globals:
        if ignored_global in global_params:
            ignored_global_params[ignored_global] = global_params[ignored_global]
            del global_params[ignored_global]

    if not disable_banner:
        banner = f"Created by BluePyOpt({bluepyopt.__version__}) at {datetime.now()}"
    else:
        banner = None

    re_init_rng = _generate_reinitrng(mechs)

    return template.render(
        template_name=template_name,
        banner=banner,
        channels=channels,
        section_params=section_params,
        range_params=range_params,
        global_params=global_params,
        re_init_rng=re_init_rng,
        replace_axon=replace_axon,
        ignored_global_params=ignored_global_params,
        add_synapses=add_synapses,
        synapses_template_name=synapses_template_name,
        syn_hoc_filename=syn_hoc_filename,
        syn_dir=syn_dir,
    )


def create_simul_hoc(
    template_dir,
    template_filename,
    add_synapses,
    hoc_paths,
    constants_args,
    protocol_definitions,
    apical_point_isec=-1,
):
    """Create createsimulation.hoc file."""
    syn_dir = hoc_paths["syn_dir_for_hoc"]
    syn_hoc_file = hoc_paths["syn_hoc_filename"]
    hoc_file = hoc_paths["hoc_filename"]

    hoc_stim_creator = HocStimuliCreator(
        protocol_definitions,
        constants_args["mtype"],
        add_synapses,
        apical_point_isec,
    )

    # load template
    template_path = os.path.join(template_dir, template_filename)
    with open(template_path, "r", encoding="utf-8") as template_file:
        template = template_file.read()
        template = jinja2.Template(template)

    # edit template
    return (
        template.render(
            hoc_file=hoc_file,
            add_synapses=add_synapses,
            syn_dir=syn_dir,
            syn_hoc_file=syn_hoc_file,
            celsius=constants_args["celsius"],
            v_init=constants_args["v_init"],
            dt=constants_args["dt"],
            template_name=constants_args["emodel"],
            gid=constants_args["gid"],
            morph_path=constants_args["morph_path"],
            stims=hoc_stim_creator.stims_hoc,
            save_recordings=hoc_stim_creator.save_recs,
            initiate_step_stimuli=hoc_stim_creator.init_step_stimuli,
            reset_step_stimuli=hoc_stim_creator.reset_step_stimuli,
            extra_recordings_vars=hoc_stim_creator.extra_recs_vars,
            extra_recordings=hoc_stim_creator.extra_recs,
        ),
        hoc_stim_creator.n_stims,
    )
