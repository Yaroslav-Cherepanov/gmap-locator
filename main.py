#!/usr/bin/env python
#
import webapp2, os, jinja2, MySQLdb,json
import urllib2, time, csv, re
import xml.etree.ElementTree as ET
from google.appengine.api import taskqueue
from google.appengine.api import urlfetch


env = os.getenv('SERVER_SOFTWARE')
if (env and env.startswith('Google App Engine/')):
  # Connecting from App Engine
    db = MySQLdb.connect(
    unix_socket='/cloudsql/gmap-locator-964:gmap', user='root', db='topcoder')
else:
    # Connecting from local computer
    db = MySQLdb.connect(
    host='173.194.109.58',
    port=3306,
    user='root', passwd='igor', db='topcoder')

JINJA_ENVIRONMENT = jinja2.Environment(
    loader=jinja2.FileSystemLoader(os.path.dirname(__file__)),
    extensions=['jinja2.ext.autoescape'],
    autoescape=True)

# Handler for '/'
class MainHandler(webapp2.RequestHandler):
    def get(self):
        template = JINJA_ENVIRONMENT.get_template('index.html')
        self.response.out.write(template.render())

# Handler for '/all'
class AllHandler(webapp2.RequestHandler):
    def select_events(self):
    
        # Select from table events: address, organization name, 
        # training names corresponding to the organization name, latitude, longitude 
        
        cursor = db.cursor(MySQLdb.cursors.DictCursor)
        query_events = '''select address, name_of_organization, group_concat(training_name_subject separator ', ') as training_names, longitude, latitude from events where longitude is not NULL group by longitude, latitude;'''
        cursor.execute(query_events)
        return list(cursor.fetchall())

    def select_projects(self):
    
        # Select from table projects: address, partner, 
        # projects corresponding to the partner, latitude, longitude
        
        cursor = db.cursor(MySQLdb.cursors.DictCursor)
        query_events = '''select address, partner, group_concat(project_name separator ', ') as projects, longitude, latitude from projects where longitude is not NULL group by longitude, latitude;'''
        cursor.execute(query_events)
        return list(cursor.fetchall())

    def select_partners(self):
    
        # Select from table partner: address, organization name, 
        # latitude, longitude
        
        cursor = db.cursor(MySQLdb.cursors.DictCursor)
        query_events = '''select address, name_of_organization, longitude, latitude from partners where longitude is not NULL;'''
        cursor.execute(query_events)
        return list(cursor.fetchall())

    def get(self):
        template = JINJA_ENVIRONMENT.get_template('map.html')
        events = self.select_events()
        partners = self.select_partners()
        projects = self.select_projects()
        locations = {'events':events, 'partners':partners, 'projects':projects, 'display':['events', 'partners', 'projects']}
        # locations['display'] shows which types of elements we are going to show
        
        self.response.out.write(template.render(locations=locations))

class UploadHandler(webapp2.RequestHandler):
    
    # Handler for importing database from three csv files (one file per excel sheet)
    # Unfortunately, it works correctly only from local computer, because of GAE execution limit for requests
    # Using Task Queue should improve the situation

    def get(self):
        template = JINJA_ENVIRONMENT.get_template('upload.html')
        self.response.out.write(template.render())

    def create_table(self, data, table_name):
        
        # Create and fill table <table_name> if it haven't been created yet
        # data type is csvreader

        # Get column names from header of csv file
        
        columns = data.next()
        columns.extend(['longitude', 'latitude'])
        
        # Prepare query
        
        format_string = 'create table {} ({} INT,'+'{} VARCHAR(150),'*(len(columns)-3)+'{} FLOAT, {} FLOAT, PRIMARY KEY(id {}));'
        format_list = [table_name]+columns
        format_list = [re.sub(r'[\s/]', '_', item) for item in format_list]
        if table_name == 'events':
            format_list.append(', Training_Name_Subject')
        elif table_name == 'projects':
            format_list.append(', Project_Name')
        else:
            format_list.append('')
        create_query = format_string.format(*format_list)
        
        # Create table

        cursor = db.cursor(MySQLdb.cursors.DictCursor)
        try:
            cursor.execute(create_query)
            db.commit()
        except:
            self.response.out.write('Maybe table {} already exists\n'.format(table_name))

        # Fill the table
        
        # Indices in row:
        country_index = columns.index('Country')
        city_index = columns.index('City')
        address_index = columns.index('Address')

        for x in data:
            if len(x)==0:
                continue

            # Geocoding using Yahoo Maps API
                
            search_str = urllib2.quote(x[country_index]+','+x[city_index]+','+x[address_index])
            geocode_url = 'http://gws2.maps.yahoo.com/findlocation?pf=1&location='+search_str
            try:
                req = urllib2.urlopen(geocode_url)
                response = req.read()
            except:
                print 'DownloadError'

            # Parsing the response

            try:
                root = ET.fromstring(response)
            except:
                print 'xml error'
                continue
            if root.find('Found').text!='0':
                lat = float(root.find('Result').find('latitude').text)
                lng = float(root.find('Result').find('longitude').text)

                # Prepare query

                insert_query = 'insert into {}'.format(table_name)+' values (%s,'+"""%s,"""*(len(columns)-3)+'%s, %s);'
                format_list = [int(x[0])]+x[1:]+[lng, lat]
            
                # Insert into table

                try:
                    cursor.execute(insert_query, format_list)
                    db.commit()
                except:
                    self.response.out.write('Insert error\n')
            
    def post(self):
        # Loading csv files to server

        events = csv.reader(self.request.POST['events'].value.split('\n'), delimiter='|')
        self.create_table(events, 'events')
        projects = csv.reader(self.request.POST['projects'].value.split('\n'), delimiter='|')
        self.create_table(projects, 'projects')
        partners = csv.reader(self.request.POST['partners'].value.split('\n'), delimiter='|')
        self.create_table(partners, 'partners')

class NearestHandler(webapp2.RequestHandler):
    def get(self):
        # Search of the nearest location of each type

        cursor = db.cursor(MySQLdb.cursors.DictCursor)

        # User coordinates 
        lat = self.request.GET['latitude']
        lng = self.request.GET['longitude']

        # Type which we will show to user 
        display_locations = [x for x in self.request.GET.keys() if x!='latitude' and x!='longitude']
        locations = {}
        locations['display']=display_locations
            
        # Prepare and execute query for each type
        # Part of the query responsible for distance calculation
        distance_comparison = """3956*2*ASIN(SQRT(POWER(SIN(({}-abs(latitude))*pi()/180 /2),2)+COS({}*pi()/180)*COS(abs(latitude)*pi()/180)*POWER(SIN(({}-longitude)*pi()/180/2),2))) as distance """.format(lat,lat,lng)
        query_string = """ select address, name_of_organization, group_concat(training_name_subject separator ', ') as training_names, longitude, latitude, """+distance_comparison+ """from events where longitude is not NULL group by longitude, latitude order by distance limit 1;"""
        cursor.execute(query_string)
        locations['events'] = list(cursor.fetchall())
        query_string = """select address, partner, group_concat(project_name separator ', ') as projects, longitude, latitude, """+distance_comparison+ """from projects where longitude is not NULL group by longitude, latitude order by distance limit 1;"""
        cursor.execute(query_string)
        locations['projects'] = list(cursor.fetchall())
        query_string = """select address, name_of_organization, longitude, latitude, """+distance_comparison+ """from partners where longitude is not NULL group by longitude, latitude order by distance limit 1;"""
        cursor.execute(query_string)
        locations['partners'] = list(cursor.fetchall())
        
        template = JINJA_ENVIRONMENT.get_template('nearest.html')
        self.response.out.write(template.render(locations=locations))

app = webapp2.WSGIApplication([
    ('/', MainHandler),
    ('/all', AllHandler),
    ('/upload', UploadHandler),
    ('/nearest', NearestHandler)
], debug=True)
