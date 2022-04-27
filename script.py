#!/usr/bin/env python
import boto3
import click
import json
import datetime
import time
from json import JSONEncoder

from main import scale_down
import os
import sys

from meraki import DashboardAPI
from meraki.exceptions import APIError

# To improve
# - assign instance size based on VMX Size (create mapping)
class DateTimeEncoder(JSONEncoder):
    #Override the default method
    def default(self, obj):
        if isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.isoformat()

MERAKI_API_KEY = ""
VMX_AMI_ID = ''

meraki = DashboardAPI(MERAKI_API_KEY, output_log=False, suppress_logging=True, be_geo_id='Boundless Digital')

aws_profile = 'boundless-sandbox'
aws_session = boto3.session.Session(profile_name=aws_profile)
ec2 = aws_session.client('ec2')
ec2resource = aws_session.resource('ec2')

def get_organization(organization_name):
    # Get Meraki Organization
    organizations = meraki.organizations.getOrganizations()
    organization_id = next((org['id'] for org in organizations if org['name'] == organization_name), None)

    if not organization_id:
        print(f"We didn't find the organization named {organization_name}.  \
            Please verify that its spelled correctly, or that your API key has permissions to access this org")
        sys.exit(1)

    return organization_id

def get_available_vmx(organization_id):
    # Find all vMXs in organization
    devices = meraki.organizations.getOrganizationInventoryDevices(organization_id, total_pages='all')
    vmx_devices = [device for device in devices if device['model'].startswith('VMX')]
    unassigned_vmx_devices = [device for device in vmx_devices if not device['networkId']]

    if not unassigned_vmx_devices:
        print('No unused vMX devices found in the "{organization_name}" organization. \
            Please verify your that you have added the appropriate licenses')
        sys.exit(1)

    device_serial = unassigned_vmx_devices[0]['serial']
    return [device_serial]

def create_appliance_network(organization_id, network_name, device_serials, timezone='Europe/Paris'):
    # Create vMX Appliance network
    product_types = ['appliance']
    try:
        response = meraki.organizations.createOrganizationNetwork(
            organization_id, network_name, product_types, timeZone=timezone
        )
        network_id, dashboard_url = response['id'], response['url']
    except APIError as e:
        print(e)
        sys.exit(1)

    # Add vMX to Network
    claim_device_response = meraki.networks.claimNetworkDevices(
        network_id, device_serials
    )

    authentication_token = meraki.appliance.createDeviceApplianceVmxAuthenticationToken(
    device_serials[0]
    )

    print("Auth token below")
    print(authentication_token)

    return network_id, authentication_token

def create_vmx_instance(network_name, network_id, authentication_token):
    vmx_instance_name = f'Cisco Meraki VMX-S - {network_name}'
    # Launch EC2 Instance
    userdata = authentication_token['token']
    instance_type = 'c5.large'

    ec2_run_instances_response = ec2.run_instances(
        ImageId=VMX_AMI_ID,
        InstanceType=instance_type,
        MinCount=1,
        MaxCount=1,
        UserData=userdata,
        TagSpecifications=[
            {
                'ResourceType': 'instance',
                'Tags': [
                    {
                        'Key': 'Name',
                        'Value': vmx_instance_name
                    },
                    {
                        'Key': 'Network',
                        'Value': network_id
                    },
                ]
            }
        ]
    )

    instance_id = ec2_run_instances_response['Instances'][0]['InstanceId']

    # Modify EC2 Instance to disable source-destination restriction
    ec2_modify_instance_response = ec2.modify_instance_attribute(
        InstanceId=instance_id,
        SourceDestCheck={
            'Value': False
        }
    )

def scale_up(organization_name, network_name):
    organization_id = get_organization(organization_name)
    device_serials = get_available_vmx(organization_id)
    network_id, authentication_token = create_appliance_network(organization_id, network_name, device_serials)
    create_vmx_instance(network_name, network_id, authentication_token)
    print("vMX Network successfully deployed!")


@click.command()
@click.option('-o', '--organization', default = 'Boundless Migration')
@click.option('-n', '--network', default= 'AWS - Automation Testing')
@click.option('--up/--down', 'grow')
def main(organization, network, grow):
        scale_up(organization, network)

if __name__ == '__main__':
    main()
