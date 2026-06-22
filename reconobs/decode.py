"""reconobs.decode - aircraft-recon text decoders.

VENDORED VERBATIM from tropycal v1.4 (tropycal/recon/tools.py): decode_hdob,
decode_vdm, decode_dropsonde. These already target the exact NHC product
formats (URNT15/URPN15 HDOB, URNT12/URPN12 VDM, URNT13/URPN13 dropsonde). We
lift them rather than reinvent (per the recon spec). Pure stdlib + numpy +
pandas; no tropycal import (its package __init__ pulls pkg_resources).

decode_hdob(content, mission_row) -> DataFrame[time,lat,lon,plane_p,plane_z,
  p_sfc,temp,dwpt,wdir,wspd,pkwnd,sfmr,rain,flag,mission,mission_id]
  mission_row=3 for archive .txt (WMO header on line 3); =2 for the live
  .shtml <pre> block (header on line 2). The `flag` column is a per-row list
  of flagged var names - ALWAYS drop rows where the plotted var is in `flag`
  before any min/max or coloring.
decode_vdm(content, date)        -> dict (center fix: lat/lon, MSLP, max winds)
decode_dropsonde(content, date)  -> dict (sounding profile + surface fix)

Upstream: https://github.com/tropycal/tropycal (MIT). Tracking tag v1.4.
"""
import numpy as np
import pandas as pd
from datetime import datetime as dt, timedelta
import re

def decode_hdob(content, mission_row=3):
    r"""
    Function for decoding HDOBs in the present format (2007-present).
    """

    # Split items by lines
    tmp = [i.split() for i in content.split('\n')]
    tmp = [i for j, i in enumerate(tmp) if len(i) > 0]
    items = []
    for j, i in enumerate(tmp):
        if j > mission_row and len(i[0]) >= 2 and i[0][:2] == "$$":
            break
        if j > mission_row + 12 and len(i[0]) >= 3 and i[0][:3] == "000":
            break  # avoid reading in extra set of HDOBs
        if j <= mission_row:
            items.append(i)
        if j > mission_row and i[0][0].isdigit() and len(i) > 3:
            items.append(i)

    # Parse dates
    missionname = items[2][1]
    data = {}
    data['time'] = [dt.strptime(
        items[2][-1] + i[0], '%Y%m%d%H%M%S') for i in items[3:]]
    if data['time'][0].hour > 12 and data['time'][-1].hour < 12:
        data['time'] = [t + timedelta(days=[0, 1][t.hour < 12])
                        for t in data['time']]

    # Parse full data
    data['lat'] = [np.nan if '/' in i[1] else round((float(i[1][:-3]) + float(i[1][-3:-1]) / 60) * [-1, 1][i[1][-1] == 'N'], 2)
                   for i in items[3:]]
    data['lon'] = [np.nan if '/' in i[2] else round((float(i[2][:-3]) + float(i[2][-3:-1]) / 60) * [-1, 1][i[2][-1] == 'E'], 2)
                   for i in items[3:]]
    data['plane_p'] = [np.nan if '/' in i[3]
                       else round(float(i[3]) * 0.1 + [0, 1000][float(i[3]) < 1000], 1) for i in items[3:]]
    data['plane_z'] = [np.nan if '/' in i[4]
                       else round(float(i[4]), 0) for i in items[3:]]
    data['p_sfc'] = [np.nan if (('/' in i[5]) | (p < 550))
                     else round(float(i[5]) * 0.1 + [0, 1000][float(i[5]) < 1000], 1) for i, p in zip(items[3:], data['plane_p'])]
    data['temp'] = [np.nan if '/' in i[6]
                    else round(float(i[6]) * 0.1, 1) for i in items[3:]]
    data['dwpt'] = [np.nan if '/' in i[7]
                    else round(float(i[7]) * 0.1, 1) for i in items[3:]]
    data['wdir'] = [np.nan if '/' in i[8][:3]
                    else round(float(i[8][:3]), 0) for i in items[3:]]
    data['wspd'] = [np.nan if '/' in i[8][3:]
                    else round(float(i[8][3:]), 0) for i in items[3:]]
    data['pkwnd'] = [np.nan if '/' in i[9]
                     else round(float(i[9]), 0) for i in items[3:]]
    data['sfmr'] = [np.nan if '/' in i[10]
                    else round(float(i[10]), 0) for i in items[3:]]
    data['rain'] = [np.nan if '/' in i[11]
                    else round(float(i[11]), 0) for i in items[3:]]

    # Fix erroneous SFMR
    data['sfmr'] = [np.nan if i > 300 else i for i in data['sfmr']]
    data['wspd'] = [np.nan if i > 300 else i for i in data['wspd']]
    data['pkwnd'] = [np.nan if i > 300 else i for i in data['pkwnd']]

    # Ignore entries with lat/lon of 0
    orig_lat = np.copy(data['lat'])
    orig_lon = np.copy(data['lon'])
    for key in data.keys():
        data[key] = [data[key][i] for i in range(
            len(orig_lat)) if orig_lat[i] != 0 and orig_lon[i] != 0]

    # Add flag
    data['flag'] = []
    for i in items[3:]:
        flag = []
        if int(i[12][0]) in [1, 3]:
            flag.extend(['lat', 'lon'])
        if int(i[12][0]) in [2, 3]:
            flag.extend(['plane_p', 'plane_z'])
        if int(i[12][1]) in [1, 4, 5, 9]:
            flag.extend(['temp', 'dwpt'])
        if int(i[12][1]) in [2, 4, 6, 9]:
            flag.extend(['wdir', 'wspd', 'pkwnd'])
        if int(i[12][1]) in [3, 5, 6, 9]:
            flag.extend(['sfmr', 'rain'])
        data['flag'].append(flag)

    # QC p_sfc
    if any(abs(np.gradient(data['p_sfc'], np.array(data['time']).astype('datetime64[s]').astype(float))) > 1):
        data['p_sfc'] = [np.nan] * len(data['p_sfc'])
        data['flag'] = [d.append('p_sfc') for d in data['flag']]

    # Identify mission number and ID
    content_split = content.split("\n")
    mission_id = '-'.join(
        (content_split[mission_row].replace("  ", " ")).split(" ")[:3])
    data['mission'] = [missionname[:2]] * len(data['time'])
    data['mission_id'] = [mission_id] * len(data['time'])

    # remove nan's for lat/lon coordinates
    return_data = pd.DataFrame.from_dict(data).reset_index()
    return_data = return_data.dropna(subset=['lat', 'lon'])

    return return_data

def decode_vdm(content, date):
    data = {}
    lines = content.split('\n')
    RemarksNext = False
    LonNext = False
    missionname = ''

    if date.year >= 2018:
        FORMAT = 1
    elif date.year >= 1999:
        FORMAT = 2
    elif date.year == 1998:
        FORMAT = 3
    else:
        FORMAT = 4

    def isNA(x):
        if x == 'NA':
            return np.nan
        else:
            try:
                return float(x)
            except:
                return x.lower()

    for line in lines:

        if RemarksNext:
            data['Remarks'] += (' ' + line)
        if LonNext:
            info = line.split()
            data['lon'] = np.round(
                (float(info[0]) + float(info[2]) / 60) * [-1, 1][info[4] == 'E'], 2)
            LonNext = False

        if 'VORTEX DATA MESSAGE' in line:
            stormid = line.split()[-1]
        if line[:2] == 'A.':
            if ':' in line[3:]:
                info = line[3:].split('/')
                day = int(info[0])
                month = (date.month - int(day - date.day > 15) - 1) % 12 + 1
                year = date.year - \
                    int(date.month - int(day - date.day > 15) == 0)
                hour, minute, second = [int(i) for i in (
                    info[1].split("Z")[0]).split(':')]
                data['time'] = dt(year, month, day, hour, minute, second)
            else:
                info = line[3:].split('/')
                day = int(info[0])
                month = (date.month - int(day - date.day > 15) - 1) % 12 + 1
                year = date.year - \
                    int(date.month - int(day - date.day > 15) == 0)
                hour = int(info[1].split("Z")[0][:2])
                minute = int(info[1].split("Z")[0][2:4])
                data['time'] = dt(year, month, day, hour, minute)

        if line[:2] == 'B.':
            info = line[3:].split()
            if FORMAT >= 2:
                data['lat'] = np.round(
                    (float(info[0]) + float(info[2]) / 60) * [-1, 1][info[4] == 'N'], 2)
                LonNext = True
            if FORMAT == 1:
                data['lat'] = float(info[0]) * [-1, 1][info[2] == 'N']
                data['lon'] = float(info[3]) * [-1, 1][info[5] == 'E']

        if line[:2] == 'C.':
            info = line[3:].split() * 5
            data[f'Standard Level (hPa)'] = isNA(info[0])
            data[f'Minimum Height at Standard Level (m)'] = isNA(info[2])

        if line[:2] == 'D.':
            info = line[3:].split() * 5
            if FORMAT >= 2:
                data['Estimated Maximum Surface Wind Inbound (kt)'] = isNA(
                    info[0])
            if FORMAT == 1:
                data['Minimum Sea Level Pressure (hPa)'] = isNA(info[-2])

        if line[:2] == 'E.':
            info = line[3:].split() * 5
            if FORMAT >= 2:
                data['Dropsonde Surface Wind Speed at Center (kt)'] = isNA(
                    info[2])
                data['Dropsonde Surface Wind Direction at Center (deg)'] = isNA(
                    info[0])
            if FORMAT == 1:
                data['Location of Estimated Maximum Surface Wind Inbound'] = isNA(
                    line[3:])

        if line[:2] == 'F.':
            info = line[3:]
            if FORMAT >= 2:
                data['Maximum Flight Level Wind Inbound'] = isNA(info)
            if FORMAT == 1:
                data['Eye character'] = isNA(info)

        if line[:2] == 'G.':
            info = line[3:]
            if FORMAT >= 2:
                data['Location of the Maximum Flight Level Wind Inbound'] = isNA(
                    info)
            if FORMAT == 1:
                if isNA(info) == np.nan:
                    data.update(
                        {'Eye Shape': np.nan, 'Eye Diameter (nmi)': np.nan})
                else:
                    shape = ''.join([i for i in info[:2] if not i.isdigit()])
                    size = info[len(shape):]
                    if shape == 'C':
                        if '-' in size:
                            data.update(
                                {'Eye Shape': 'circular', 'Eye Diameter (nmi)': float(size.split('-')[0])})
                        else:
                            data.update({'Eye Shape': 'circular',
                                        'Eye Diameter (nmi)': float(size)})
                    elif shape == 'CO':
                        data['Eye Shape'] = 'concentric'
                        if '-' in size:
                            data.update({f'Eye Diameter {i+1} (nmi)': float(s)
                                        for i, s in enumerate(size.split('-'))})
                        else:
                            data.update({f'Eye Diameter {i+1} (nmi)': float(s)
                                        for i, s in enumerate(size.split(' ')[1:])})
                    elif shape == 'E':
                        einfo = size.split('/')
                        data.update({'Eye Shape': 'elliptical', 'Orientation': float(einfo[0]) * 10,
                                     'Eye Major Axis (nmi)': float(einfo[1]), 'Eye Minor Axis (nmi)': float(einfo[1])})
                    else:
                        data.update(
                            {'Eye Shape': np.nan, 'Eye Diameter (nmi)': np.nan})

        if line[:2] == 'H.':
            info = line[3:].split() * 5
            if FORMAT >= 3:
                info = (line[3:].split("MB")[0]).split()
                if info[0] == '/':
                    data['Minimum Sea Level Pressure (hPa)'] = np.nan
                else:
                    parsed_mslp = info[-1]
                    if '/' in parsed_mslp:
                        parsed_mslp = parsed_mslp.split("/")[1]
                    data['Minimum Sea Level Pressure (hPa)'] = isNA(
                        parsed_mslp)
            elif FORMAT == 2:
                data['Minimum Sea Level Pressure (hPa)'] = isNA(info[-2])
            elif FORMAT == 1:
                data['Estimated Maximum Surface Wind Inbound (kt)'] = isNA(
                    info[0])

        if line[:2] == 'I.':
            info = line[3:]
            if FORMAT >= 2:
                data['Maximum Flight Level Temp Outside Eye (C)'] = isNA(
                    info.split()[0])
            if FORMAT == 1:
                data['Location & Time of the Estimated Maximum Surface Wind Inbound'] = isNA(
                    info)

        if line[:2] == 'J.':
            info = line[3:]
            if FORMAT >= 2:
                data['Maximum Flight Level Temp Inside Eye (C)'] = isNA(
                    info.split()[0])
            if FORMAT == 1:
                data['Maximum Flight Level Wind Inbound (kt)'] = isNA(info)

        if line[:2] == 'K.':
            info = line[3:]
            if FORMAT >= 2:
                data['Dew Point Inside Eye (C)'] = isNA(info.split()[0])
            if FORMAT == 1:
                data['Location & Time of the Maximum Flight Level Wind Inbound'] = isNA(
                    info)

        if line[:2] == 'L.':
            info = line[3:]
            if FORMAT >= 2:
                data['Eye character'] = isNA(info)
            if FORMAT == 1:
                data['Estimated Maximum Surface Wind Outbound (kt)'] = isNA(
                    info)

        if line[:2] == 'M.':
            info = line[3:]
            if FORMAT >= 2:
                if isNA(info) == np.nan:
                    data.update(
                        {'Eye Shape': np.nan, 'Eye Diameter (nmi)': np.nan})
                else:
                    shape = ''.join([i for i in info[:2] if not i.isdigit()])
                    size = info[len(shape):]
                    if shape == 'C':
                        if '-' in size:
                            data.update(
                                {'Eye Shape': 'circular', 'Eye Diameter (nmi)': float(size.split('-')[0])})
                        else:
                            data.update({'Eye Shape': 'circular',
                                        'Eye Diameter (nmi)': float(size)})
                    elif shape == 'CO':
                        data['Eye Shape'] = 'concentric'
                        if '-' in size:
                            data.update({f'Eye Diameter {i+1} (nmi)': float(s)
                                        for i, s in enumerate(size.split('-'))})
                        else:
                            data.update({f'Eye Diameter {i+1} (nmi)': float(s)
                                        for i, s in enumerate(size.split(' ')[1:])})
                    elif shape == 'E':
                        einfo = size.split('/')
                        try:
                            data.update({'Eye Shape': 'elliptical', 'Orientation': float(einfo[0]) * 10,
                                         'Eye Major Axis (nmi)': float(einfo[1]), 'Eye Minor Axis (nmi)': float(einfo[1])})
                        except:
                            data.update({'Eye Shape': 'elliptical', 'Orientation': np.nan,
                                         'Eye Major Axis (nmi)': np.nan, 'Eye Minor Axis (nmi)': np.nan})
                    else:
                        data.update(
                            {'Eye Shape': np.nan, 'Eye Diameter (nmi)': np.nan})
            if FORMAT == 1:
                data['Location & Time of the Estimated Maximum Surface Wind Outbound'] = isNA(
                    info)

        if line[:2] == 'N.':
            info = line[3:]
            if FORMAT == 1:
                data['Maximum Flight Level Wind Outbound (kt)'] = isNA(info)

        if line[:2] == 'O.':
            info = line[3:]
            if FORMAT == 1:
                data['Location & Time of the Maximum Flight Level Wind Outbound'] = isNA(
                    info)

        if line[:2] == 'P.':
            info = line[3:]
            if FORMAT >= 2:
                data['Aircraft'] = info.split()[0]
                missionname = info.split()[1]
                data['mission'] = missionname[:2]
                data['Remarks'] = ''
                RemarksNext = True
            if FORMAT == 1:
                data['Maximum Flight Level Temp & Pressure Altitude Outside Eye'] = isNA(
                    info)

        if line[:2] == 'Q.':
            info = line[3:]
            if FORMAT == 1:
                data['Maximum Flight Level Temp & Pressure Altitude Inside Eye'] = isNA(
                    info)
            if FORMAT == 3:
                data['Aircraft'] = info.split()[0]
                missionname = info.split()[1]
                data['mission'] = missionname[:2]
                data['Remarks'] = ''

        if line[:2] == 'R.':
            info = line[3:]
            if FORMAT == 1:
                data['Dewpoint Temp (collected at same location as temp inside eye)'] = isNA(
                    info)

        if line[:2] == 'S.':
            info = line[3:]
            if FORMAT == 1:
                data['Fix'] = isNA(info)

        if line[:2] == 'T.':
            info = line[3:]
            if FORMAT == 1:
                data['Accuracy'] = isNA(info)

        if line[:2] == 'U.':
            info = line[3:]
            if FORMAT == 1:
                data['Aircraft'] = info.split()[0]
                missionname = info.split()[1]
                data['mission'] = missionname[:2]
                data['Remarks'] = ''
                RemarksNext = True

    content_split = content.split("\n")
    if FORMAT == 4:
        mission_id = '-'.join(content_split[1].replace("  ",
                              " ").split(" ")[:3])
        data['Aircraft'] = mission_id.split("-")[0]
        missionname = mission_id.split("-")[1]
        data['mission'] = missionname[:2]
        data['Remarks'] = ''
    elif FORMAT == 3:
        mission_id = ['-'.join(i.split("Q. ")[1].replace("  ", " ").split(" ")[:3])
                      for i in content_split if i[:2] == "Q."][0]
    elif FORMAT == 2:
        mission_id = ['-'.join(i.split("P. ")[1].replace("  ", " ").split(" ")[:3])
                      for i in content_split if i[:2] == "P."][0]
    elif FORMAT == 1:
        mission_id = ['-'.join(i.split("U. ")[1].replace("  ", " ").split(" ")[:3])
                      for i in content_split if i[:2] == "U."][0]
    data['mission_id'] = mission_id

    return missionname, data

def decode_dropsonde(content, date):

    NOLOCFLAG = False
    missionname = '_____'

    delimiters = ['XXAA', '31313', '51515',
                  '61616', '62626', 'XXBB', '21212', '_____']
    sections = {}
    for i, d in enumerate(delimiters[:-1]):
        a = content.split('\n' + d)
        if len(a) > 1:
            a = ('\n' + d).join(a[1:]) if len(a) > 2 else a[1]
            b = a.split('\n' + delimiters[i + 1])[0]
            sections[d] = b

    for k, v in sections.items():
        tmp = copy.copy(v)
        for d in delimiters:
            tmp = tmp.split('\n' + d)[0]
        tmp = [i for i in tmp.split(' ') if len(i) > 0]
        tmp = [j.replace('\n', '') if '\n' in j and (len(j) < (
            7 + j.count('\n')) or len(j) == (11 + j.count('\n'))) else j for j in tmp]
        tmp = [i for j in tmp for i in j.split('\n') if len(i) > 0]
        sections[k] = tmp

    def _time(timestr):
        try:
            if timestr < f'{date:%H%M}':
                return date.replace(hour=int(timestr[:2]), minute=int(timestr[2:4]))
            else:
                return date.replace(hour=int(timestr[:2]), minute=int(timestr[2:4])) - timedelta(days=1)
        except:
            return None

    def _tempdwpt(item):
        if '/' in item[:3]:
            temp = np.nan
            dwpt = np.nan
        elif '/' in item[4:]:
            z = round(float(item[:3]), 0)
            temp = round(z * 0.1, 1) if z % 2 == 0 else round(z * -0.1, 1)
            dwpt = np.nan
        else:
            z = round(float(item[:3]), 0)
            temp = round(z * 0.1, 1) if z % 2 == 0 else round(z * -0.1, 1)
            z = round(float(item[3:]), 0)
            dwpt = temp - (round(z * 0.1, 1) if z <= 50 else z - 50)
        return temp, dwpt

    def _wdirwspd(item):
        try:
            wdir = round(
                np.floor(float(item[:3]) / 5) * 5, 0) if '/' not in item else np.nan
            wspd = round(float(item[3:]) + 100 * (float(item[2]) %
                         5), 0) if '/' not in item else np.nan
        except:
            wdir = np.nan
            wspd = np.nan
        return wdir, wspd

    def _standard(I3):
        levkey = I3[0][:2]
        levdict = {'99': -1, '00': 1000, '92': 925, '85': 850, '70': 700, '50': 500,
                   '40': 400, '30': 300, '25': 250, '20': 200, '15': 150, '10': 100, '__': None}
        pres = float(levdict[levkey])
        output = {}
        output['pres'] = pres
        if pres == -1:
            output['pres'] = np.nan if '/' in I3[0][2:] else round(
                float(I3[0][2:]) + [0, 1000][float(I3[0][2:]) < 100], 1)
            output['hgt'] = 0.0
        elif pres == 1000:
            z = np.nan if '/' in I3[0][2:] else round(float(I3[0][2:]), 0)
            output['hgt'] = round(500 - z, 0) if z >= 500 else z
        elif pres == 925:
            output['hgt'] = np.nan if '/' in I3[0][2:
                                                   ] else round(float(I3[0][2:]), 0)
        elif pres == 850:
            output['hgt'] = np.nan if '/' in I3[0][2:
                                                   ] else round(float(I3[0][2:]) + 1000, 0)
        elif pres == 700:
            z = np.nan if '/' in I3[0][2:] else round(float(I3[0][2:]), 0)
            output['hgt'] = round(
                z + 3000, 0) if z < 500 else round(z + 2000, 0)
        elif pres in (500, 400, 300):
            output['hgt'] = np.nan if '/' in I3[0][2:
                                                   ] else round(float(I3[0][2:]) * 10, 0)
        else:
            output['hgt'] = np.nan if '/' in I3[0][2:] else round(
                1e4 + float(I3[0][2:]) * 10, 0)
        output['temp'], output['dwpt'] = _tempdwpt(I3[1])
        if I3[2][:2] == '88':
            output['wdir'], output['wspd'] = np.nan, np.nan
            skipflag = 0
        elif I3[2][:2] == list(levdict.keys())[list(levdict.keys()).index(levkey) + 1] and \
                I3[3][:2] != list(levdict.keys())[list(levdict.keys()).index(levkey) + 1]:
            output['wdir'], output['wspd'] = np.nan, np.nan
            skipflag = 1
        else:
            output['wdir'], output['wspd'] = _wdirwspd(I3[2])
            skipflag = 0
        endflag = True if '88' in [i[:2] for i in I3] else False
        return output, skipflag, endflag

    data = {k: np.nan for k in ('lat', 'lon', 'slp',
                                'TOPlat', 'TOPlon', 'TOPtime',
                                'BOTTOMlat', 'BOTTOMlon', 'BOTTOMtime',
                                'MBLdir', 'MBLspd', 'DLMdir', 'DLMspd',
                                'WL150dir', 'WL150spd', 'top', 'LSThgt', 'software', 'levels')}
    data['TOPtime'] = None
    data['BOTTOMtime'] = None

    for sec, items in sections.items():

        if sec == '61616' and len(items) > 0:
            missionname = items[1]
            data['mission'] = items[1][:2]
            data['stormname'] = items[2]
            try:
                data['obsnum'] = int(items[-1])
            except:
                data['obsnum'] = items[-1]

        if sec == 'XXAA' and len(items) > 0 and not NOLOCFLAG:
            if '/' in items[1] + items[2]:
                NOLOCFLAG = True
            else:
                octant = int(items[2][0])
                data['lat'] = round(float(items[1][2:]) *
                                    0.1 * [-1, 1][octant in (2, 3, 7, 8)], 1)
                data['lon'] = round(float(items[2][1:]) *
                                    0.1 * [-1, 1][octant in (0, 1, 2, 3)], 1)
                data['slp'] = np.nan if '/' in items[4][2:] else round(
                    float(items[4][2:]) + [0, 1000][float(items[4][2:]) < 100], 1)

                standard = {k: []
                            for k in ['pres', 'hgt', 'temp', 'dwpt', 'wdir', 'wspd']}
                skips = 0
                for jj, item in enumerate(items[4::3]):
                    if items[4 + jj * 3 - skips][:2] == '88':
                        break
                    output, skipflag, endflag = _standard(
                        items[4 + jj * 3 - skips:8 + jj * 3 - skips])
                    skips += skipflag
                    for k in standard.keys():
                        standard[k].append(output[k])
                    if endflag:
                        break
                standard = pd.DataFrame.from_dict(
                    standard).sort_values('pres', ascending=False)

        if sec == '62626' and len(items) > 0 and not NOLOCFLAG:
            if items[0] in ['CENTER', 'MXWNDBND', 'RAINBAND', 'EYEWALL']:
                data['location'] = items[0]
                if items[0] == 'EYEWALL':
                    data['octant'] = {'000': 'N', '045': 'NE', '090': 'E', '135': 'SE',
                                      '180': 'S', '225': 'SW', '270': 'W', '315': 'NW'}[items[1]]
            if 'REL' in items:
                tmp = items[items.index('REL') + 1]
                data['TOPlat'] = round(
                    float(tmp[:4]) * .01 * [-1, 1][tmp[4] == 'N'], 2)
                data['TOPlon'] = round(
                    float(tmp[5:10]) * .01 * [-1, 1][tmp[10] == 'E'], 2)
                tmp = items[items.index('REL') + 2]
                if data['TOPtime'] is None:
                    data['TOPtime'] = _time(tmp)
            if 'SPG' in items:
                tmp = items[items.index('SPG') + 1]
                data['BOTTOMlat'] = round(
                    float(tmp[:4]) * .01 * [-1, 1][tmp[4] == 'N'], 2)
                data['BOTTOMlon'] = round(
                    float(tmp[5:10]) * .01 * [-1, 1][tmp[10] == 'E'], 2)
                tmp = items[items.index('SPG') + 2]
                if data['BOTTOMtime'] is None:
                    data['BOTTOMtime'] = _time(tmp)
            elif 'SPL' in items:
                tmp = items[items.index('SPL') + 1]
                data['BOTTOMlat'] = round(
                    float(tmp[:4]) * .01 * [-1, 1][tmp[4] == 'N'], 2)
                data['BOTTOMlon'] = round(
                    float(tmp[5:10]) * .01 * [-1, 1][tmp[10] == 'E'], 2)
                tmp = items[items.index('SPL') + 2]
                if data['BOTTOMtime'] is None:
                    data['BOTTOMtime'] = _time(tmp)
            if 'MBL' in items:
                tmp = items[items.index('MBL') + 2]
                wdir, wspd = _wdirwspd(tmp)
                data['MBLdir'] = wdir
                data['MBLspd'] = wspd
            if 'DLM' in items:
                tmp = items[items.index('DLM') + 2]
                wdir, wspd = _wdirwspd(tmp)
                data['DLMdir'] = wdir
                data['DLMspd'] = wspd
            if 'WL150' in items:
                tmp = items[items.index('WL150') + 1]
                wdir, wspd = _wdirwspd(tmp)
                data['WL150dir'] = wdir
                data['WL150spd'] = wspd
            if 'LST' in items:
                tmp = items[items.index('LST') + 2]
                tmp = tmp.replace('=', '')
                data['LSThgt'] = round(float(tmp), 0)
            if 'AEV' in items:
                tmp = items[items.index('AEV') + 1]
                data['software'] = 'AEV ' + tmp

        if sec == 'XXBB' and len(items) > 0 and not NOLOCFLAG:
            sigtemp = {k: [] for k in ['pres', 'temp', 'dwpt']}
            for jj, item in enumerate(items[6::2]):
                z = np.nan if '/' in items[6 + jj *
                                           2][2:] else round(float(items[6 + jj * 2][2:]), 0)
                sigtemp['pres'].append(round(z + 1000, 0) if z < 100 else z)
                temp, dwpt = _tempdwpt(items[7 + jj * 2])
                sigtemp['temp'].append(temp)
                sigtemp['dwpt'].append(dwpt)
            sigtemp = pd.DataFrame.from_dict(
                sigtemp).sort_values('pres', ascending=False)

        if sec == '21212' and len(items) > 0 and not NOLOCFLAG:
            sigwind = {k: [] for k in ['pres', 'wdir', 'wspd']}
            for jj, item in enumerate(items[2::2]):
                z = np.nan if '/' in items[2 + jj *
                                           2][2:] else round(float(items[2 + jj * 2][2:]), 0)
                sigwind['pres'].append(round(z + 1000, 0) if z < 100 else z)
                wdir, wspd = _wdirwspd(items[3 + jj * 2])
                sigwind['wdir'].append(wdir)
                sigwind['wspd'].append(wspd)
            sigwind = pd.DataFrame.from_dict(
                sigwind).sort_values('pres', ascending=False)

        if sec == '31313' and len(items) > 0 and not NOLOCFLAG:
            tmp = [i for i in items if i[0] == '8'][0]
            data['TOPtime'] = _time(tmp[1:])

    if not NOLOCFLAG:
        def _justify(a, axis=0):
            mask = pd.notnull(a)
            arg_justified = np.argsort(mask, axis=0)[-1]
            anew = [col[i] for i, col in zip(arg_justified, a.T)]
            return anew
        df = pd.concat([standard, sigtemp, sigwind], ignore_index=True,
                       sort=False).sort_values('pres', ascending=False)
        data['levels'] = pd.DataFrame(np.vstack(df.groupby('pres', sort=False)
                                                .apply(lambda gp: _justify(gp.to_numpy()))), columns=df.columns)

        data['top'] = np.nanmin(data['levels']['pres'])

    content_split = content.split("\n")
    try:
        mission_id = ['-'.join(i.split("61616 ")[1].replace("  ", " ").split(" ")[:3])
                      for i in content_split if i[:5] == "61616"][0]
    except:
        mission_id = '-'.join(content.split("\n")
                              [1].replace("  ", " ").split(" ")[:3])
    data['mission_id'] = mission_id

    # Fix NaNs
    if data['BOTTOMtime'] is None:
        data['BOTTOMtime'] = np.nan
    if data['TOPtime'] is None:
        data['TOPtime'] = np.nan

    return missionname, data

