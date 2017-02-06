import re
import unicodecsv
headers = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']
parties = ['(Democrat)', '(Republican)']
party_abbrevs = ['(REP)', '(DEM)', '(GRN)', '(PRO)', '(LIB)', '(CON)', '(PAC)', '(LBT)', '(WFP)', '(PGP)', '(PCE)', '(IND)']
offices = ['President', 'US Senator', 'US Representative, 5th District', 'Representative in Congress, 4th District', 'State Representative, 23rd     District', 'State Representative, 23rd       District',
'State Senator,   5th District', 'State Representative, 10th       District', 'Governor', 'State Representative, 23rd      District', 'State Representative, 23rd District', 'State Representative, 20th       District',
'State Treasurer', 'Attorney General', 'Secretary of State', 'State Senator, 1st District', 'State Representative, 20th      District', 'Representative, Congress, 4th District', 'United States President and Vice President',
'Representative in Congress 4th District', 'State Senator, 5th District', 'State Senator 4th District', 'State Representative, 20th District', 'State Senator, 12th District', ]
office_lookup = {
    'US Senator' : 'U.S. Senate', 'US Representative' : 'U.S. House', 'Governor' : 'Governor', 'State Senator' : 'State Senate',
    'State Representative' : 'State House', 'Secretary of State' : 'Secretary of State', 'Attorney General' : 'Attorney General',
    'State Treasurer' : 'State Treasurer', 'Representative' : 'U.S. House', 'United States President' : 'President', 'Representative, Congress' : 'U.S. House',
    'President' : 'President', 'Representative in Congress 4th District': 'U.S. House', 'Representative, Congress 2nd Dist': 'U.S. House'
}

with open('20160517__or__primary__polk__precinct.csv', 'wb') as csvfile:
    w = unicodecsv.writer(csvfile, encoding='utf-8')
    w.writerow(headers)

    lines = open('/Users/derekwillis/Downloads/polk_primary_2016.txt').readlines()
    keys = []
    for line in lines:
        if line.strip() == '\n':
            continue
        if "PRECINCT REPORT" in line:
            continue
        if line.strip() == '':
            continue
        if 'RUN TIME:' in line:
            continue
        if 'Run Date:' in line:
            continue
        if 'STATISTICS' in line:
            continue
        if 'VOTER   TURNOUT' in line:
            continue
        if '- - -' in line:
            continue
        if '-----' in line:
            continue
        if '==' in line:
            continue
        if "REGISTERED VOTERS" in line:
            continue
        if "BALLOTS CAST" in line:
            continue
        if "VOTER TURNOUT" in line:
            continue
        if 'VOTERS' in line:
            continue
        if 'PERCENT' in line:
            continue
        if 'Election' in line:
            continue
        if 'of the' in line:
            continue
        if line.strip() == 'Vote for 1':
            continue
        if line.strip().split("    ")[0:3] == [u'01', u'02', u'03']:
            continue
        # for primary
        if any(party in line for party in parties):
            party = line.replace("(",'').replace(")",'').replace('*','').strip()
            continue
        # parse offices, reset keys
        if any(office in line for office in offices):
            if "DISTRICT" in line.strip().upper():
                if 'State Senator' in line.strip():
                    o = 'State Senator'
                    d = line.strip().split('State Senator')[1]
                elif 'State Representative' in line.strip():
                    o = 'State Representative'
                    d = line.strip().split('State Representative')[1]
                elif line.strip() == 'US Representative, 5th District':
                    o = 'US Representative'
                    d = '5th District'
                else:
                    o, d = line.strip().split(', ')
                office = office_lookup[o.strip()]
                district = "".join([s for s in d if s.isdigit()])
            else:
                office = office_lookup[line.strip()]
                district = None
            keys = []
            continue
        if line.strip().replace(' ','').isdigit():
            precinct = line.strip().split(' ')[1]
            continue
        if "." in line:
            if office:
                # get candidate keys
                candidate = line.split('.')[0].strip()
                votes = line.split('.',1)[1].replace(' . ','').strip().split('   ')[-3:]
                votes = [v for v in votes if v != '']
                w.writerow(['Polk', precinct, office, district, party, candidate, int(votes[0])])
