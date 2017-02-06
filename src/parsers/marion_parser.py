import unicodecsv
headers = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']
parties = ['DEMOCRAT', 'REPUBLICAN', 'Democrat', 'Republican']
party_abbrevs = ['(REF)','(LIB)','(IND)','(PGN)','(REP)', '(DEM)', '(GRN)', '(PRO)', '(CON)', '(PAC)', '(LBT)', '(WFP)', '(PGP)', '(PCE)', '(IND)', '(REP,IND)', '(DEM,WFP)','(PGP,PRO)', '(DEM,IND)', '(PEP)', '(WFM)', '(PGN)']
offices = ['US PRESIDENT', 'US SENATOR', 'U S REPRESENTATIVE IN CONGRESS - 5TH DISTRICT', 'GOVERNOR', 'STATE SENATOR', 'STATE REPRESENTATIVE', 'SECRETARY OF STATE', 'ATTORNEY GENERAL', 'STATE TREASURER']
office_lookup = {
    'US SENATOR' : 'U.S. Senate', 'U S REPRESENTATIVE IN CONGRESS' : 'U.S. House', 'GOVERNOR' : 'Governor', 'STATE SENATOR' : 'State Senate',
    'STATE REPRESENTATIVE' : 'State House', 'SECRETARY OF STATE' : 'Secretary of State', 'ATTORNEY GENERAL' : 'Attorney General', 'US PRESIDENT': 'President',
    'STATE TREASURER' : 'State Treasurer'
}

def skip_check(line):
    p = False
    if 'PRIMARY ELECTION' in line:
        p = True
    elif line.strip() == 'NOVEMBER':
        p = True
    elif line.strip() == '\n':
        p = True
    elif " of 53" in line:
        p = True
#    elif "US REPRESENTATIVE" in line:
#        p = True
#    elif "UNITED STATES REPRESENTATIVE IN CONGRESS" in line:
#        p = True
    elif "FINAL OFFICIAL" in line:
        p = True
    elif "NUMBERED KEY CANVASS" in line:
        p = True
    elif line.strip() == '':
        p = True
    elif 'RUN DATE:' in line:
        p = True
    elif 'STATISTICS' in line:
        p = True
    elif '- - -' in line:
        p = True
    elif '-----' in line:
        p = True
    elif '==' in line:
        p = True
    elif 'PERCENT' in line:
        p = True
    elif 'ELECTION' in line:
        p = True
    elif 'REGISTERED VOTERS' in line:
        p = True
    elif 'BALLOTS CAST' in line:
        p = True
    elif 'of the' in line:
        p = True
    elif 'Vote For' in line.strip():
        p = True
    elif line.strip().split("    ")[0:3] == [u'01', u'02', u'03']:
        p = True
    return p

def office_check(line):
    if "DIST" in line.strip():
        if ',' in line.strip():
            o, d = line.strip().split(', ')
            district = "".join([s for s in d if s.isdigit()])
        elif '-' in line.strip():
            p, o, d = line.strip().split(' - ')
            district = "".join([s for s in d if s.isdigit()])
        else:
            o, d = line.strip().split(' DISTRICT')
            district = "".join([s for s in d if s.isdigit()])
#        if 'dist' in line.strip():
#            o, d = line.strip().split(', ')
#        else:
#            o1, o2, d = line.strip().split(', ')
#            o = o1.strip() + ', ' + o2.strip()
        office = office_lookup[o]
    else:
        if '-' in line.strip():
            office = office_lookup[line.strip().split(' - ')[1]]
            district = None
        else:
            office = office_lookup[line.strip()]
            district = None
    return [office, district]

def cand_check(line):
    temp_keys = []
    candidate_bits = [x.strip() for x in line.split('   ') if '=' in x]
    candidates = [x.split(' = ') for x in candidate_bits]
    for code, name in candidates:
#        if any(party in line for party in party_abbrevs):
#            party = next(party for party in party_abbrevs if party in line)
#            if not party in name:
#                if '(PAC)' in name:
#                    party = 'PAC'
#                    name = name.replace('(PAC)', '').strip()
#                elif '(WFP)' in name:
#                    party = 'WFP'
#                    name = name.replace('(WFP)', '').strip()
#                elif '(DEM)' in name:
#                    party = 'DEM'
#                    name = name.replace('(DEM)', '').strip()
#            else:
#                name = name.replace(party, '').strip()
#                party = party.replace('(','').replace(')','')
#        else:
#            party = None
#        if name in ['WRITE-IN', 'OVER VOTES', 'UNDER VOTES', 'REGISTERED VOTERS - TOTAL', 'BALLOTS CAST - TOTAL ', 'VOTER TURNOUT - TOTAL']:
#            party = None
        temp_keys.append({'code': int(code), 'name': name, 'party': 'DEM'})
    return temp_keys

def process_line(line, keys, w, party):
    if '=' in line:
        temp_keys = cand_check(line)
        for key in temp_keys:
            keys.append(key)

    else:
        if len(keys) > 0:
            # once we have all keys, sort them in order
            candidate_keys = sorted(keys, key=lambda k: k['code'])
            # parse vote data, should match keys
            result_bits = [x for x in line.split('  ') if x <> '']
            result_bits = [x.strip() for x in result_bits]
            result_bits = [x.replace(' . ', ' ') for x in result_bits]
            precinct = result_bits[0]
            if len(result_bits) > 0:
                for cand in candidate_keys:
                    try:
                        w.writerow(['Marion', precinct, office, district, cand['party'], cand['name'], result_bits[cand['code']]])
                    except:
                        print result_bits
                        raise
                        #w.writerow(['Multnomah', precinct, 'Total', None, cand['party'], cand['name'], result_bits[cand['code']]])
#        else:
#            pass

with open('20000516__or__primary__marion__precinct2.csv', 'wb') as csvfile:
    w = unicodecsv.writer(csvfile, encoding='utf-8')
    w.writerow(headers)

    lines = open('marion.txt').readlines()
    keys = []
    for line in lines:
        if 'Commissioner' in line:
            break
        if 'Superintendent' in line:
            break
        if 'Judge' in line:
            break
        if skip_check(line):
            continue
        if any(party in line for party in parties):
            party = line.strip()[0:3]
            continue
        if any(office in line for office in offices):
            office, district = office_check(line)
            keys = []
        try:
            process_line(line, keys, w, party=None)
        except:
            raise
