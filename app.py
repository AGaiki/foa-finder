#!/Users/emuckley/Documents/Github/foa-finder/webenv/bin/python

# -*- coding: utf-8 -*-


"""

What this script does:
(1) finds the latest FOA database on grants.gov
(2) downloads the database
(3) unzips the database to an xml file
(4) converts the xml database into a pandas dataframe
(5) filters the FOA dataframe by keywords
(6) sends the filtered FOA list to a dedicated channel on Slack.

python version to use for crontab scheduling in virtual environment:
Users/emuckley/Documents/GitHub/foa-finder/webenv/bin/python


crontab script to run every 24 hours at 18:05 hrs:
5 18 * * * /Users/emuckley/Documents/GitHub/foa-finder/app.py >> ~/cron.log 2>&1


"""


import os
import json
import time
import zipfile
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup

pd.options.mode.chained_assignment = None


# %%%%%%%%%%%%%%%%%%%% find the database %%%%%%%%%%%%%%%%%%%%%%%%%%


def get_xml_url_and_filename():
    """Get the URL and filename of the most recent XML database file
    posted on grants.gov."""

    day_to_try = datetime.today()

    file_found = None
    while file_found is None:
        url = 'https://www.grants.gov/extract/GrantsDBExtract{}v2.zip'.format(
            day_to_try.strftime('%Y%m%d'))
        response = requests.get(url, stream=True)

        # look back in time if todays data is not posted yet
        if response.status_code == 200:
            file_found = url
        else:
            day_to_try = day_to_try - timedelta(days=1)

        filename = 'GrantsDBExtract{}v2.zip'.format(
            day_to_try.strftime('%Y%m%d'))

    print('Found database file {}'.format(filename))

    return url, filename


# get url and filename of the latest database available for extraction
url, filename = get_xml_url_and_filename()


# %%%%%%%%%%%%%%%%%%%% download the database %%%%%%%%%%%%%%%%%%%%%%%%%%


def download_file_from_url(url, filename):
    """Download a file from a URL"""
    # remove all previously-downloaded zip files
    [os.remove(f) for f in os.listdir() if f.endswith(".zip")]
    # ping the dataset url
    response = requests.get(url, stream=True)
    # if file url is found
    if response.status_code == 200:
        handle = open(filename, "wb")
        for chunk in response.iter_content(chunk_size=512):
            if chunk:  # filter out keep-alive new chunks
                handle.write(chunk)
        handle.close()
        time.sleep(3)
        print('Database successfully downloaded')
    # if file url is not found
    else:
        print('URL does not exist')


# download the database zip file
download_file_from_url(url, filename)


# %%%%%%%%%%%%%%%%%%%%%%%%% unzip and parse file %%%%%%%%%%%%%%%%%%%%%


def unzip_and_soupify(filename, unzipped_dirname='unzipped'):
    """Unzip a zip file and parse it using beautiful soup"""

    # if unzipped directory doesn't exist, create it
    if not os.path.exists(unzipped_dirname):
        os.makedirs(unzipped_dirname)

    # remove all previously-downloaded zip files
    for f in os.listdir(unzipped_dirname):
        os.remove(os.path.join(unzipped_dirname, f))

    # unzip raw file
    with zipfile.ZipFile(filename, "r") as z:
        z.extractall(unzipped_dirname)

    # get path of file in unzipped folder
    unzipped_filepath = os.path.join(
        unzipped_dirname,
        os.listdir(unzipped_dirname)[0])

    print('Unzipping {}'.format(unzipped_filepath))

    # parse as tree and convert to string
    tree = ET.parse(unzipped_filepath)
    root = tree.getroot()
    doc = str(ET.tostring(root, encoding='unicode', method='xml'))
    # initialize beautiful soup object
    soup = BeautifulSoup(doc, 'lxml')
    print('Database unzipped')

    return soup


# get beautiful soup object from parsed zip file
soup = unzip_and_soupify(filename)


# %%%%%%%%%%%% populate dataframe with every xml tag %%%%%%%%%%%%%%%%%%%%


def soup_to_df(soup):
    """Convert beautifulsoup object from grants.gov XML into dataframe"""
    # list of bs4 FOA objects
    s = 'opportunitysynopsisdetail'
    foa_objs = [tag for tag in soup.find_all() if s in tag.name.lower()]

    # loop over each FOA in the database and save its details as a dictionary
    dic = {}
    for i, foa in enumerate(foa_objs):
        ch = foa.findChildren()
        dic[i] = {}
        for fd in ch:
            if fd.name.split('ns0:')[1] in dic[i].keys():
                dic[i][fd.name.split('ns0:')[1]] += ',' + fd.text
            else:
                dic[i][fd.name.split('ns0:')[1]] = fd.text

    # create dataframe from dictionary
    df = pd.DataFrame.from_dict(dic, orient='index')
    return df


# get full dataframe of all FOAs
dff = soup_to_df(soup)


# %%%%%%%%%%%%%%%%%% filter by dates and keywords %%%%%%%%%%%%%%%%%%%%%%%%

def to_date(date_str):
    """Convert date string from database into date object"""
    return datetime.strptime(date_str, '%m%d%Y').date()


def is_recent(date, days=90):
    """Check if date occured within n amount of days from today"""
    return (datetime.today().date() - to_date(date)).days <= days


def is_open(date):
    """Check if FOA is still open (closedate is in the past)"""
    if type(date) == float:
        return True
    elif type(date) == str:
        return (datetime.today().date() - to_date(date)).days <= 0


def reformat_date(s):
    """Reformat the date string with hyphens so its easier to read"""
    s = str(s)
    return s[4:]+'-'+s[:2]+'-'+s[2:4]


def sort_by_recent_updates(df):
    """Sort the dataframe by recently updated dates"""
    new_dates = [reformat_date(i) for i in df['lastupdateddate']]
    df.insert(1, 'updatedate', new_dates)
    df = df.sort_values(by=['updatedate'], ascending=False)
    print('Database sorted and filtered by date')
    return df

from nltk.corpus import wordnet as wn

def filter_by_keywords(df):
    """Filter the dataframe by keywords.
    The keywords are set in an external csv file called
    'keywords.csv'."""
    # get keywords to filter dataframe
    keywords = list(pd.read_csv('keywords testing.csv', header=None)[0])
    keywords_str = '|'.join(keywords).lower()

    hypernyms_set = set()

    for i in keywords:
        if len(wn.synsets(i.lower())) == 0:
            continue
        hypernyms = wn.synsets(i.lower())[0].hypernyms()
        for j in hypernyms:
            hypernyms_set.add(j.lemma_names()[0])

    keywords_str += '|' + '|'.join(hypernyms_set)

    keywords_str = keywords_str.replace('_', ' ')

    print(keywords_str)
    # get non-keywords to avoid

    # filter by post date - the current year and previous year only
    # curr_yr = np.max([int(i[-4:]) for i in df['postdate'].values])
    # prev_yr = curr_yr - 1
    # df = df[df['postdate'].str.contains('-'+str(curr_yr), na=False)]

    # filter dataframe by keywords
    df = df[df['description'].str.contains(keywords_str, na=False)] 

    print('Database filtered by keywords')

    return df


def filter_by_eligibility(df):
    for_profit = 22
    small_business = 23
    unrestricted = 99
    #others = 25

    search_categories1 = str(for_profit) + '|' + str(small_business) + '|' + str(unrestricted) # + '|' + str(others)

    df = df[df['eligibleapplicants'].str.contains(search_categories1, na=False)]

    print('Database filtered by eligibility')
    
    return df

def filter_by_category(df):
    energy = 'EN'
    transportation = 'T'
    agriculture = 'AG'
    business_and_commerce = 'BC'
    
    search_categories2 = energy + '|' + transportation + '|' + agriculture + '|' + business_and_commerce
    
    df = df[df['categoryoffundingactivity'].str.contains(search_categories2, na=False)]
    
    print('Database filtered by funding category')
    
    return df
    
def filter_by_relevance(df):
    relevance = list(np.zeros(len(df)))
    
    keywords = list(pd.read_csv('keywords testing.csv', header=None)[0])

    hypernyms_set = set()

    for i in keywords:
        if len(wn.synsets(i.lower())) == 0:
            continue
        hypernyms = wn.synsets(i.lower())[0].hypernyms()
        for j in hypernyms:
            hypernyms_set.add(j.lemma_names()[0].replace('_', ' '))

    # filter dataframe by keywords
    for key in set(keywords).union(hypernyms_set):
        contains = list(df['description'].str.contains(key, na=False))
        for i in range(len(contains)):
            if contains[i]:
                relevance[i] += 1

    df['relevance'] = relevance
    df = df.sort_values(by=['relevance'], ascending=False)

    print('Database filtered by relevance')

    return df

# include only recently updated FOAs
df = dff[[is_recent(i) for i in dff['lastupdateddate']]]

# include only FOAs which are not closed
df = df[[is_open(i) for i in df['closedate']]]

# sort by newest FOAs at the top
df = sort_by_recent_updates(df)

# filter by keywords
df = filter_by_keywords(df)

# filter by eligibility
df = filter_by_eligibility(df)

# filter by category
df = filter_by_category(df)

# filter by relevance
df = filter_by_relevance(df)

# %%%%%%%%%%%%%%% format string message for Slack %%%%%%%%%%%%%%%%%%%%%%


def create_slack_text(filename, df, print_text=True):
    """Create text to send into Slack"""
    # get database date
    db_date = filename.split('GrantsDBExtract')[1].split('v2')[0]
    db_date = db_date[:4]+'-'+db_date[4:6]+'-'+db_date[6:]

    # create text
    slack_text = 'Showing {} recently updated FOAs from grants.gov, extracted {}:'.format(
        len(df), db_date)
    slack_text += '\n======================================='

    base_hyperlink = r'https://www.grants.gov/web/grants/search-grants.html?keywords='

    # loop over each FOA title and add to text
    for i in range(len(df)):

        hyperlink = base_hyperlink + df['opportunitynumber'].iloc[i]

        slack_text += '\n{}) Updated: {},  Closes: {}, Title: {}, {} ({}) \n{}'.format(
            i+1,
            df['updatedate'].iloc[i],
            reformat_date(df['closedate'].iloc[i]),
            df['opportunitytitle'].iloc[i].upper(),
            df['opportunitynumber'].iloc[i],
            df['opportunityid'].iloc[i],
            hyperlink)
        slack_text += '\n----------------------------------'

    slack_text += '\nFOAs filtered by date and keywords in {}'.format(
        'https://github.com/ericmuckley/foa-finder/blob/master/keywords.csv.')

    if print_text:
        print(slack_text)
    else:
        print('Slack text generated')
    return slack_text


# create the text to send to slack
slack_text = create_slack_text(filename, df)


# %%


def send_to_slack(slack_text):
    """Send results to a channel on Slack"""
    print('Sending results to slack')
    try:
        response = requests.post(
            #insert hook here,
            data=json.dumps({'text': slack_text}),
            headers={'Content-Type': 'application/json'})
        print('Slack response: ' + str(response.text))
    except:
        print('Connection to Slack could not be established.')


# send text to slack
send_to_slack(slack_text)
