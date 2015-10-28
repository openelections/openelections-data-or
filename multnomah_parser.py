import re
import unicodecsv
import requests
from BeautifulSoup import BeautifulSoup

headers = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']

parties = ['DEMOCRAT', 'REPUBLICAN']

party_abbrevs = ['(REP)', '(DEM)', '(GRN)', '(PRO)', '(LIB)', '(CON)', '(PAC)', '(LBT)', '(WFP)', '(PGP)', '(PCE)', '(IND)']

offices = ['United States President and Vice President', 'United States President', 'United States Senator', 'Representative in Congress, 1st District', 'Representative in Congress, 3rd District',
'State Representative, 27th District', 'State Representative, 31st District', 'State Representative, 33rd District', 'State Representative, 35th District', 'Governor',
'State Treasurer', 'Attorney General', 'Secretary of State', 'State Senator, 14th District', 'State Senator, 17th District', 'State Senator, 18th District',
'State Senator, 21st District', 'State Senator, 22nd District', 'State Senator, 23rd District', 'State Senator, 25th District', 'Representative in Congress, 5th District',
'State Representative, 36th District', 'State Representative, 38th District', 'State Representative, 41st District', 'State Representative, 42nd District', 'State Representative, 43rd District',
'State Representative, 44th District', 'State Representative, 45th District', 'State Representative, 46th District', 'State Representative, 47th District', 'State Representative, 48th District',
'State Representative, 49th District', 'State Representative, 50th District', 'State Representative, 51st District', 'State Representative, 52nd District', 'United States Representative, 3rd Dist.',
'United States Representative, 1st Dist.', 'United States Representative, 5th Dist.', 'UNITED STATES PRESIDENT/VICE PRESIDENT', 'UNITED STATES SENATOR', 'UNITED STATES REPRESENTATIVE DIST 1',
'UNITED STATES REPRESENTATIVE DIST 3', 'UNITED STATES REPRESENTATIVE DIST 5', 'STATE TREASURER', 'ATTORNEY GENERAL', 'STATE SENATOR DIST 14', 'STATE SENATOR DIST 25', 'STATE SENATOR DIST 18',
'STATE SENATOR DIST 21', 'STATE SENATOR DIST 22', 'STATE SENATOR DIST 23', 'STATE REPRESENTATIVE DIST 27', 'STATE REPRESENTATIVE DIST 31', 'STATE REPRESENTATIVE DIST 33', 'STATE REPRESENTATIVE DIST 35',
'STATE REPRESENTATIVE DIST 36', 'STATE REPRESENTATIVE DIST 38', 'STATE REPRESENTATIVE DIST 41', 'STATE REPRESENTATIVE DIST 42', 'STATE REPRESENTATIVE DIST 43', 'STATE REPRESENTATIVE DIST 44',
'STATE REPRESENTATIVE DIST 45', 'STATE REPRESENTATIVE DIST 46', 'STATE REPRESENTATIVE DIST 47', 'STATE REPRESENTATIVE DIST 48', 'STATE REPRESENTATIVE DIST 49', 'STATE REPRESENTATIVE DIST 50',
'STATE REPRESENTATIVE DIST 51', 'STATE REPRESENTATIVE DIST 52', 'SECRETARY OF STATE', 'UNITED STATES PRESIDENT']

office_lookup = {
    'United States Senator' : 'U.S. Senate', 'United States Representative' : 'U.S. House', 'Governor' : 'Governor', 'State Senator' : 'State Senate',
    'State Representative' : 'State House', 'Secretary of State' : 'Secretary of State', 'Attorney General' : 'Attorney General', 'UNITED STATES PRESIDENT/VICE PRESIDENT': 'President',
    'State Treasurer' : 'State Treasurer', 'Representative' : 'U.S. House', 'United States President' : 'President', 'United States Representative, 5th Dist.' : 'U.S. House',
    'United States President' : 'President', 'Representative in Congress, 3rd District': 'U.S. House', 'United States Representative, 1st Dist.': 'U.S. House',
    'United States President and Vice President': 'President', 'Representative in Congress, 5th District' : 'U.S. House', 'United States Representative, 3rd Dist.': 'U.S. House',
    'UNITED STATES SENATOR': 'U.S. Senate', 'UNITED STATES REPRESENTATIVE': 'U.S. House', 'SECRETARY OF STATE' : 'Secretary of State', 'UNITED STATES PRESIDENT': 'President',
    'STATE TREASURER' : 'State Treasurer', 'ATTORNEY GENERAL': 'Attorney General', 'STATE SENATOR': 'State Senate', 'STATE REPRESENTATIVE': 'State House'
}

def skip_check(line):
    p = False
    if line.strip() == 'General Election':
        p = True
    elif line.strip() == '\n':
        p = True
    elif "US REPRESENTATIVE" in line:
        p = True
    elif "UNITED STATES REPRESENTATIVE IN CONGRESS" in line:
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
    elif 'of the' in line:
        p = True
    elif 'VOTE FOR' in line.strip():
        p = True
    elif line.strip().split("    ")[0:3] == [u'01', u'02', u'03']:
        p = True
    return p

def office_check(line):
    if "DIST" in line.strip():
        o, d = line.strip().split(' DIST ')
#        if 'dist' in line.strip():
#            o, d = line.strip().split(', ')
#        else:
#            o1, o2, d = line.strip().split(', ')
#            o = o1.strip() + ', ' + o2.strip()
        office = office_lookup[o]
        district = d#"".join([s for s in d if s.isdigit()])
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
        temp_keys.append({'code': int(code), 'name': name, 'party': party})
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
                        w.writerow(['Multnomah', precinct, office, district, cand['party'], cand['name'], result_bits[cand['code']]])
                    except:
                        continue
                        #w.writerow(['Multnomah', precinct, 'Total', None, cand['party'], cand['name'], result_bits[cand['code']]])
    #    except:
    #        pass

with open('20040518__or__primary__multnomah__precinct.csv', 'wb') as csvfile:
    w = unicodecsv.writer(csvfile, encoding='utf-8')
    w.writerow(headers)

    r = requests.get('https://multco.us/elections/may-18-2004-abstracts')
    soup = BeautifulSoup(r.text)
    lines = soup.find('pre').text.split('\n')
    keys = []
    for line in lines:
        if 'COMMISSIONER' in line:
            break
        if 'JUDGE' in line:
            break
        if skip_check(line):
            continue
        if any(party in line for party in parties):
            party = line.replace("(",'').replace(")",'').strip()
        if any(office in line for office in offices):
            office, district = office_check(line)
            print office
            keys = []
        process_line(line, keys, w, party)
