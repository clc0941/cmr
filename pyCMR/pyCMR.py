import json
import logging
from hs3_meta_data import metaDataTool
import os
from collectionMetadata import CollectionCMRXMLTags
from granuleMetadata import GranuleCMRXMLTags

import shutil
from datetime import datetime
import xml.etree.ElementTree as ET
try:
    from configparser import ConfigParser
except ImportError:
    from ConfigParser import ConfigParser

import requests

from Result import Collection, Granule
from xmlParser import XmlDictConfig, ComaSeperatedToListJson,ComaSeperatedDataToListJson


class CMR(object):
    def __init__(self, configFilePath=''):
        """
        :param configFilePath: The config file containing the credentials to make CRUD requests to CMR (extention .cfg)
        These con
        """
        self.config = ConfigParser()
        if os.path.isfile(configFilePath) and os.access(configFilePath, os.R_OK | os.W_OK):
            # Open the config file as normal
            self.config.read(configFilePath)
            self.configFilePath = configFilePath
        elif not os.path.isfile(configFilePath) and \
            set(['CMR_PROVIDER', 'CMR_USERNAME', 'CMR_PASSWORD', 'CMR_CLIENT_ID']).issubset(set(os.environ.keys())):
            logging.info("Creating new config file, using information in the `CMR_*` environment variables")

            pycmr_base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            example_config_path = os.path.join(pycmr_base_dir, 'cmr.cfg.example')

            if not os.path.isfile((example_config_path)):
                with open(os.path.join(pycmr_base_dir, 'cmr.cfg.example'), 'w') as e:
                    e.write(base_cfg)
                    e.close()

            new_config_path = os.path.join(pycmr_base_dir, 'cmr.cfg')
            shutil.copyfile(example_config_path, new_config_path)
            configFilePath = new_config_path

            self.config.read(configFilePath)
            self.configFilePath = configFilePath
            self.config.set('credentials', 'provider', os.environ['CMR_PROVIDER'])
            self.config.set('credentials', 'username', os.environ['CMR_USERNAME'])
            self.config.set('credentials', 'password', os.environ['CMR_PASSWORD'])
            self.config.set('credentials', 'client_id', os.environ['CMR_CLIENT_ID'])
            self.config.write(open(self.configFilePath, 'w'))
            logging.info("Config file created, at {}".format(new_config_path))
        else:
            raise IOError("The config file can't be opened for reading/writing")

        self._PAGE_SIZE = self.config.getint("request", "page_size")
        self._SEARCH_GRANULE_URL = self.config.get("request", "search_granule_url")
        self._SEARCH_COLLECTION_URL = self.config.get("request", "search_collection_url")

        self._INGEST_URL = self.config.get("request", "ingest_url")
        self._REQUEST_TOKEN_URL = self.config.get("request", "request_token_url")

        self._PROVIDER = self.config.get("credentials", "provider")
        self._USERNAME = self.config.get("credentials", "username")
        self._PASSWORD = self.config.get("credentials", "password")
        self._CLIENT_ID = self.config.get("credentials", "client_id")

        self._ECHO_TOKEN = self.config.get("credentials", "echo_token")




        self._createSession()
        if not self.config.get('credentials', 'ECHO_TOKEN'):
            self._generateNewToken()

        self._CONTENT_TYPE = self.config.get("request", "content_type")
        self._INGEST_HEADER = {'Content-type': self._CONTENT_TYPE}
        self._SEARCH_HEADER = {'Accept': self._CONTENT_TYPE}
        self._CMR_HOST = self.config.get("request", "cmr_host")

    def _get_search_results(self, url, limit, **kwargs):
        """
        Search the CMR granules
        :param limit: limit of the number of results
        :param kwargs: search parameters
        :return: list of results (<Instance of Result>)
        """
        logging.info("======== Waiting for response ========")

        page_num = 1
        results = []
        while len(results) < limit:
            response = requests.get(
                url=url,
                params=dict(kwargs, page_num=page_num, page_size=self._PAGE_SIZE),
                headers=self._SEARCH_HEADER
            )
            unparsed_page = response.content
            page = ET.XML(unparsed_page)

            empty_page = True
            for child in list(page):
                if child.tag == 'result':
                    results.append(XmlDictConfig(child))
                    empty_page = False
                elif child.tag == 'error':
                    raise ValueError('Bad search response: {}'.format(unparsed_page))

            if empty_page:
                break
            else:
                page_num += 1

        return results

    def searchGranule(self, limit=100, **kwargs):
        results = self._get_search_results(url=self._SEARCH_GRANULE_URL, limit=limit, **kwargs)
        return [Granule(result) for result in results][:limit]

    def searchCollection(self, limit=100, **kwargs):
        """
        Search the CMR collections
        :param limit: limit of the number of results
        :param kwargs: search parameters
        :return: list of results (<Instance of Result>)
        """
        results = self._get_search_results(url=self._SEARCH_COLLECTION_URL, limit=limit, **kwargs)
        return [Collection(result, self._CMR_HOST) for result in results][:limit]

    def isTokenExpired(self):
        """
        purpose: check if the token has been expired
        :return: True if the token has been expired; False otherwise.
        """
        url = self._INGEST_URL + self._PROVIDER + "/collections/PYCMR_TEST"
        putGranule = requests.put(url=url, headers=self.session.headers)
        list_ = ['Token', 'expired', 'exists']  # if the token expired or does not exists
        if (len(putGranule.text.split('<error>')) > 1):  # if there is an error in the request
            if any(word in putGranule.text for word in list_):
                return True

        return False

    def _getDataSetId(self, pathToXMLFile):
        """
        Purpose : a private function to parse the xml file and returns the dataset ID
        :param pathToXMLFile:
        :return:  the dataset id
        """
        if os.path.isfile(pathToXMLFile):

            tree = ET.parse(pathToXMLFile)
        else:
            tree=ET.fromstring(pathToXMLFile)
        try:
            return tree.find("DataSetId").text
        except:
            raise KeyError("Could not find <DataSetId> tag")

    def _getShortName(self, pathToXMLFile):
        """
        Purpose : a private function to parse the xml file and returns the datasetShortName
        :param pathToXMLFile:
        :return:  the datasetShortName
        """
        tree = ET.parse(pathToXMLFile)
        try:
            return tree.find("Collection").find("ShortName").text
        except:
            raise KeyError("Could not find <ShortName> tag")

    def _getGranuleUR(self, data):
        """
            Purpose : a private function to parse the xml file and returns the datasetShortName
            :param pathToXMLFile:
            :return:  the datasetShortName
            """

        try:
            return data.find("GranuleUR").text
        except:
            raise KeyError("Could not find <GranuleUR> tag")





    def ingestCollection(self, XMLData):
        """
        :purpose : ingest the collections using cmr rest api
        :param XMLData: a parameter that holds the XML data that needs to be ingested it can be a file
        :return: the ingest collection request if it is successfully validated
        """

        if not XMLData:
            return False

        if os.path.isfile(XMLData):
            data = self._getXMLData(pathToXMLFile=XMLData)
        else:
            data=XMLData

        dataset_id = self._getDataSetId(pathToXMLFile=XMLData)
        url = self._INGEST_URL + self._PROVIDER + "/collections/" + dataset_id
        validationRequest = self._validateCollection(data=data, dataset_id=dataset_id)
        if validationRequest.ok:  # if the collection is valid
            if self.isTokenExpired():  # check if the token has been expired
                self._generateNewToken()
            putCollection = self.session.put(url=url, data=data, headers=self._INGEST_HEADER)  # ingest granules

            return putCollection.content

        else:
            raise ValueError("Collection failed to validate:\n{}".format(validationRequest.content))

    def updateCollection(self, pathToXMLFile):
        return self.ingestCollection(XMLData=pathToXMLFile)

    def deleteCollection(self, dataset_id):
        """
        Delete an existing colection
        :param dataset_id: the collection id
        :return: response content of the deletion request
        """
        if self.isTokenExpired():  # check if the token has been expired
            self._generateNewToken()
        url = self._INGEST_URL + self._PROVIDER + "/collections/" + dataset_id
        removeCollection = self.session.delete(url)
        return removeCollection.content

    def __ingestGranuleData(self, data,granule_ur):
        validateGranuleRequest = self._validateGranule(data=data,
                                                       granule_ur=granule_ur)
        url = self._INGEST_URL + self._PROVIDER + "/granules/" + granule_ur

        if validateGranuleRequest.ok:
            if self.isTokenExpired():
                self._generateNewToken()
            putGranule = self.session.put(url=url, data=data, headers=self._INGEST_HEADER)

            return putGranule.content

        else:
            raise ValueError("Granule failed to validate:\n{}".format(validateGranuleRequest.content))




    def ingestGranule(self, XMLData):
        """
        :purpose : ingest granules using cmr rest api
        :XMLData XML data to engist to cmr:
        :return: the ingest granules request if it is successfully validated
        """

        response=[]
        if not XMLData:
            print "Error occurred while ingesting this granule; Please check if the granule exists  and if you have the right to ingest to CMR"
            return False
        if os.path.isfile(XMLData):
            tree = ET.parse(XMLData)
            root = tree.getroot()
        else:
            root = ET.fromstring(XMLData)



        for data in root.iter('Granule'):
            granule_ur = self._getGranuleUR( data=data)

            response.append(self.__ingestGranuleData(data=ET.tostring(data), granule_ur=granule_ur))

        return response



    def _getdata(self, data, keyword):
        try :
            return data[keyword]
        except:

            return None





    def generateCMRXMLTags(self, top, data):
        for key, value in data.items():
            child=ET.Element(top,key)
            child.text=value
        return top





    def fromJsonToXML(self, data):
        """
        :purpose Convert json format to XML format
        :param data: Json data
        :return: XML data
        """
        today=datetime.now()

        #====Top level tag =====
        top = ET.Element("Granule")
        GranuleUR = ET.SubElement(top, "GranuleUR")
        GranuleUR.text = data['granule_name']
        InsertTime = ET.SubElement(top, "InsertTime")
        InsertTime.text=today.strftime("%Y-%m-%dT%H:%M:%SZ")
        LastUpdate = ET.SubElement(top, "LastUpdate")
        LastUpdate.text =today.strftime("%Y-%m-%dT%H:%M:%SZ")
        Collection = ET.SubElement(top, "Collection")
        DataSetId = ET.SubElement(Collection, "DataSetId")
        DataSetId.text = self._getdata(data, 'DataSetId')



        # =============DataGranule tag ========================#
        DataGranule = ET.Element("DataGranule")
        SizeMBDataGranule = ET.SubElement(DataGranule, "SizeMBDataGranule")
        SizeMBDataGranule.text=self._getdata(data, 'size')
        DayNightFlag = ET.SubElement(DataGranule, "DayNightFlag")
        DayNightFlag.text = "UNSPECIFIED"
        ProductionDateTime = ET.SubElement(DataGranule, "ProductionDateTime")
        ProductionDateTime.text = today.strftime("%Y-%m-%dT%H:%M:%SZ")
        if SizeMBDataGranule.text:
            SizeMBDataGranule.text= str(int(SizeMBDataGranule.text)* 10E-6) # Convert to MiB units
            top.append(DataGranule)




        # =============Temporal tag ========================#
        Temporal = ET.Element("Temporal")
        RangeDateTime=ET.SubElement(Temporal,"RangeDateTime")
        BeginningDateTime=ET.SubElement(RangeDateTime,"BeginningDateTime")
        BeginningDateTime.text =self._getdata(data,'start_date')
        EndingDateTime=ET.SubElement(RangeDateTime,"EndingDateTime")
        EndingDateTime.text =  self._getdata(data,'start_date')
        BeginningDateTime.text = self._getdata(data,'start_date')
        top.append(Temporal)

        #=============Spatial tag ========================#


        Spatial=ET.Element("Spatial")
        HorizontalSpatialDomain=ET.SubElement(Spatial, "HorizontalSpatialDomain")
        Geometry=ET.SubElement(HorizontalSpatialDomain, "Geometry")
        BoundingRectangle=ET.SubElement(Geometry, "BoundingRectangle")
        WestBoundingCoordinate=ET.SubElement(BoundingRectangle, "WestBoundingCoordinate")
        WestBoundingCoordinate.text= self._getdata(data,'WLon')
        NorthBoundingCoordinate=ET.SubElement(BoundingRectangle, "NorthBoundingCoordinate")
        NorthBoundingCoordinate.text= self._getdata(data,'NLat')
        EastBoundingCoordinate=ET.SubElement(BoundingRectangle, "EastBoundingCoordinate")
        EastBoundingCoordinate.text= self._getdata(data,'ELon')
        SouthBoundingCoordinate=ET.SubElement(BoundingRectangle, "SouthBoundingCoordinate")
        SouthBoundingCoordinate.text= self._getdata(data,'SLat')
        if None not in [SouthBoundingCoordinate.text,EastBoundingCoordinate.text,WestBoundingCoordinate.text,NorthBoundingCoordinate.text]:
            top.append(Spatial)











        Orderable = ET.SubElement(top, "Orderable")
        Orderable.text = "true"

        return ET.tostring(top)

    def ingestIphexHiwrapeData(self, rootDir):
        meta = metaDataTool()
        data = meta.processIphexHiwrapeData(rootDir=rootDir)
        self.ingestGranuleTextFile(data=data)



    def ingestGranuleTextFile(self, pathToTextFile=None, data=None):
        """
        :purpose : ingest granules using cmr rest api
        :param pathToTextFile: a comma seperated values text file

        :return: logs of the requests and the overall successful ingestions
        """

        if data==None:
            listargs = ComaSeperatedToListJson(pathToFile=pathToTextFile) # convert comma seperated text file into list of json data
        else:
            listargs=ComaSeperatedDataToListJson(data=data)

        returnList = []
        errorCount = 0

        for ele in listargs: # for each element in list of json data
            xmldata = self.fromJsonToXML(ele) # convert from json to xml

            print(xmldata)

            data = self.__ingestGranuleData(data=xmldata, granule_ur=ele['granule_name']) # ingest each granule


            returnList.append(data)




            if (data.status_code >= 400): # if there is an error during the ingestion
                errorCount += 1 # increment the counter
            returnList.append(data.content)

        return {'logs': returnList,
                'result': str(len(listargs) - errorCount) + " successful ingestion out of " + str(len(listargs))}

    def _validateCollection(self, data, dataset_id):
        """
        :purpose : To validate the colection before the actual ingest
        :param data: the collection data
        :param dataset_id:
        :return: the request to validate the ingest of the collection
        """
        url = self._INGEST_URL + self._PROVIDER + "/validate/collection/" + dataset_id
        response = self.session.post(url=url, data=data, headers=self._INGEST_HEADER)
        return response

    def _validateGranule(self, data, granule_ur):
        url = self._INGEST_URL + self._PROVIDER + "/validate/granule/" + granule_ur
        response = self.session.post(url, data=data, headers=self._INGEST_HEADER)
        return response

    def _getEchoToken(self):
        """
        purpose : Requesting a new token
        :return: the new token
        """
        top = ET.Element("token")
        username = ET.SubElement(top,"username")
        username.text = self._USERNAME
        psw = ET.SubElement(top,"password")
        psw.text = self._PASSWORD
        client_id = ET.SubElement(top,"client_id")
        client_id.text = self._CLIENT_ID
        user_ip_address = ET.SubElement(top,"user_ip_address")
        user_ip_address.text = self._getIPAddress()
        #provider = ET.SubElement(top,"provider")
        #provider.text = self._PROVIDER

        data = ET.tostring(top)
        logging.info("Requesting and setting up a new token... Please wait...")
        response = requests.post(url=self._REQUEST_TOKEN_URL, data=data, headers={'Content-Type': 'application/xml'})
        if response.ok:
            return response.text.split('<id>')[1].split('</id>')[0]
        else:
            raise ValueError("New Token failed to be generated:\n{}".format(response.content))










    def updateGranule(self, pathToXMLFile):
        return self.ingestGranule(XMLData=pathToXMLFile)

    def deleteGranule(self, granule_ur):
        """
        :param granule_ur: The granule name
        :return: the content of the deletion request
        """
        if self.isTokenExpired():
            self._generateNewToken()

        url = self._INGEST_URL + self._PROVIDER + "/granules/" + granule_ur
        removeGranule = self.session.delete(url)

        return removeGranule.content

    def _getIPAddress(self):
        """
        Get the public IP address of the machine running the program
        (used to request ECHO token)
        :return: machine's public IP
        """
        response = requests.get('http://httpbin.org/ip')
        ip_address = json.loads(response.text)['origin']
        return ip_address

    def _getXMLData(self, pathToXMLFile):
        ''' Read all text from the XML file '''
        with open(pathToXMLFile, 'r') as xml_file:
            data = xml_file.read()
            return data


    def ingestNetCDFFiles(self, rootDir, ds_short_name, versionId=1):
        metaData=metaDataTool()
        xmldata=metaData.getMetaData(rootDir=rootDir, ds_short_name=ds_short_name, versionId=versionId)



        return self.ingestGranule(xmldata)










    def _generateNewToken(self):
        """
        replacing the expired token by a new one in the config file
        :return:
        """
        logging.info("Replacing the Echo Token")
        theNewToken = self._getEchoToken()
        self.config.set('credentials', 'ECHO_TOKEN',theNewToken)
        self.config.write(open(self.configFilePath, 'w'))
        self._ECHO_TOKEN = theNewToken
        self.session.headers.update({'Echo-Token': self._ECHO_TOKEN})

    def _createSession(self):
        ''' Create a new request session for the CMR object '''
        self.session = requests.Session()
        self.session.headers.update({
            'Client-Id': self._CLIENT_ID,
            'Echo-Token': self._ECHO_TOKEN
        })

base_cfg = """[credentials]
provider =
username =
password =
client_id =
echo_token =

[request]
request_token_url = https://api-test.echo.nasa.gov/echo-rest/tokens/
content_type = application/echo10+xml
cmr_host = cmr.uat.earthdata.nasa.gov

ingest_url = https://%(cmr_host)s/ingest/providers/

page_size = 50
search_granule_url = https://%(cmr_host)s/search/granules
search_collection_url = https://%(cmr_host)s/search/collections"""






if __name__=="__main__":
    cmr=CMR("../cmr.cfg.example")
    metaData = metaDataTool(
        metaDataURLResources="http://ec2-54-201-117-192.us-west-2.compute.amazonaws.com/api/v2/ghrc_catalog_dev/_table/cm_idims.ds_urls?filter=ds_short_name=",
        metaDataAPI="&api_key=8736e7dca88416f8c818d57a1e65e0c8b96075b42f911354a32b14b7ef80d317")
    #print metaData.getMetaData(rootDir="/home/marouane/Documents/IPHEX/",ds_short_name="hs3cpl", versionId=1)
    #print cmr.searchCollection(concept_id="C1216373824-GHRC")
    #print cmr.deleteCollection(dataset_id="GPM GROUND VALIDATION MET ONE RAIN GAUGE PAIRS IFLOODS V2 V2")
    #print cmr.deleteCollection(dataset_id="GPM Ground Validation NASA EPFL-LTE Parsivel DSD Data Lausanne, Switzerland V1")
    ghrcc = CollectionCMRXMLTags(configFilePath="../cmr.cfg.example")
    cXMLData=ghrcc.generateCollectionXMLToIngest(ds_short_name="msut4jj")

    #print cXMLData
    #print cmr.ingestCollection(XMLData=cXMLData)


    #ghrcg=GranuleCMRXMLTags(configFilePath="../cmr.cfg.example")
    #gXMLData = ghrcg.generateGranuleXMLToIngest(granule_name="tc4_ampr_20070806_ghrc_v2d.txt.gz")
    #print gXMLData
    #XMLsData=ghrcg.getMultipleGranulesXML(ds_short_name='gpmepfl')


    #print XMLsData

    #for ele in XMLsData:
    #    print cmr.ingestGranule(XMLData=ele)




    #cmr.deleteCollection(dataset_id='GPM Ground Validation High Altitude Imaging Wind and Rain Airborne Profiler (HIWRAP) IPHEx')
    #print cXMLData
    #print gXMLData
    #print cmr.searchCollection(ShortName='gpmepfl')
    #print len(cmr.searchGranule(ShortName='gpmepfl', limit=250))

    #print cmr.ingestGranule(XMLData=gXMLData)
    #print cmr.isTokenExpired()
    #print(cmr.ingestNetCDFFiles(rootDir="/home/marouane/Documents/IPHEX/",ds_short_name="A2_RainOcn_NRNB", versionId=2))
    #print cmr.ingestGranuleTextFile(pathToTextFile="/home/marouane/Downloads/dataexample.txt")
    #print(cmr.ingestGranule(XMLData="/home/marouane/Desktop/GHRCg__gpmrgnaifld2.xml"))
    #print cmr.ingestCollection("/home/marouane/Desktop/GHRCc_gpmepfl.xml")


    #print cmr.ingestGranule("/home/marouane/Documents/xmls/onegranule.xml")
    #print cmr.ingestCollection("/home/marouane/Documents/xmls/GHRCc_gpmrgnaifld2.xml")

