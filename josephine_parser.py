import unicodecsv
from BeautifulSoup import BeautifulSoup
headers = ['county', 'precinct', 'office', 'district', 'party', 'candidate', 'votes']
parties = ['(DEMOCRAT)', '(REPUBLICAN)', '(NONPARTISAN)']
offices = ['United States Senator', 'Representative in Congress', 'State Representative', 'Governor']

with open('20140520__or__primary__josephine__precinct.csv', 'wb') as csvfile:
    w = unicodecsv.writer(csvfile, encoding='utf-8')
    w.writerow(headers)

    file = open("/Users/DW-Admin/code/openelections-sources-or/Josephine/May14.htm").read()
    soup = BeautifulSoup(file)
    lines = soup.find('pre').text.split('\r\n')
    for line in lines:
        if line.strip() == '\n':
            continue
        if "NUMBERED KEY CANVASS" in line:
            continue
        if lines[1][0:10].strip() == '':
            continue
        if 'RUN DATE:' in line:
            continue
        if '- - -' in line:
            continue
        if "REGISTERED VOTERS" or "BALLOTS CAST" or "VOTER TURNOUT" in line:
            continue
        if line.strip() == 'Vote For  1':
            continue
        # parse offices
        if "=" in line:
            # get candidate keys
            candidate_bits = [x.strip() for x in line.split('   ') if '=' in x]
            candidates = [x.split(' = ') for x in candidate_bits]
            for code, name in candidates:
                keys.append({'code': code, 'name': name})
        if line.strip().split("    ")[0:3] == [u'01', u'02', u'03']:
            continue
        # once we have all keys, sort them in order
        candidate_keys = sorted(keys, key=lambda k: k['code'])
        print candidate_keys
        # parse vote data, should match keys
        result_bits = [x for x in line.split(' ') if x <> '']
        result_bits = result_bits[1:] # remove dupe precinct
        w.writerow(result_bits)
