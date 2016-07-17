#! /Users/dwbelliston/.virtualenvs/aws_ebs/bin/python

#Overview:
#    Take unencrypted root volume and encrypt it for EC2.
#Params:
#    ID for EC2 instance
#    Customer Master Key (CMK) (optional)
#Conditions:
#    Return if volume already encrypted
#    Use named profiles from credentials file

import sys
import boto3
import botocore
import argparse

def main(argv):
    parser = argparse.ArgumentParser(description='Encrypts EC2 root volume.')
    parser.add_argument('-i', '--instance', help='Instance to encrypt volume on.',required=True)
    parser.add_argument('-key','--customer_master_key',help='Customer master key', required=False)
    parser.add_argument('-p','--profile',help='Profile to use', required=False)
    args = parser.parse_args()

    # Set up AWS Session + Client + Resources + Waiters
    if args.profile:
        # Create custom session
        print('Using Profile {}'.format(args.profile))
        session = boto3.session.Session(profile_name=args.profile)
    else:
        # Use default session
        session = boto3.session.Session()

    client = session.client('ec2')
    ec2 = session.resource('ec2')

    waiter_instance_exists = client.get_waiter('instance_exists')
    waiter_snapshot_complete = client.get_waiter('snapshot_completed')
    waiter_volume_available = client.get_waiter('volume_available')

    # Get Instance
    instance_id = args.instance
    print('---Instance {}'.format(instance_id))
    instance = ec2.Instance(instance_id)

    # Set the max_attempts for this waiter (default 40)
    waiter_instance_exists.config.max_attempts = 1

    try:
        waiter_instance_exists.wait(
            InstanceIds=[
                instance_id,
            ]
        )
    except botocore.exceptions.WaiterError as e:
        sys.exit('ERROR: {}'.format(e))


    # Get CMK
    customer_master_key = args.customer_master_key

    ###### Steps:
    # Check if root volume is encrypted, bail if yes
    volumes = [v for v in instance.volumes.all()]
    volume_id = volumes[0].id
    volume_encrypted = volumes[0].encrypted
    if volume_encrypted:
        print('**Volume ' + volume_id + ' is already encrypted')
        sys.exit(0)
    print('**Encrypting volume ' + volume_id + '...')

    # 1.Shut down if running
    if instance.state['Code'] is 16:
        instance.stop()

    # 2.Take snapshot
    print('---Create snapshot of volume {}'.format(volume_id))
    snapshot = ec2.create_snapshot(
        VolumeId=volume_id,
        Description='Snapshot of {}'.format(volume_id),
    )

    waiter_snapshot_complete.wait(
        SnapshotIds=[
            snapshot.id,
        ]
    )

    # 3.Create new encrypted volume(same size)
    print('---Create Encrypted Snapshot Copy')
    if customer_master_key:
        # Use custom key
        snapshot_encrypted = snapshot.copy(
            SourceRegion=session.region_name,
            Description='Encrypted Copied Snapshot of {}'.format(snapshot.id),
            KmsKeyId=customer_master_key,
            Encrypted=True,
        )
    else:
        # Use default key
        snapshot_encrypted = snapshot.copy(
            SourceRegion=session.region_name,
            Description='Encrypted Copied Snapshot of {}'.format(snapshot.id),
            Encrypted=True,
        )

    waiter_snapshot_complete.wait(
        SnapshotIds=[
            snapshot_encrypted['SnapshotId'],
        ],
    )

    print('---Create Encrypted Volume from snapshot')
    volume_encrypted = ec2.create_volume(
        SnapshotId=snapshot_encrypted['SnapshotId'],
        AvailabilityZone=instance.placement['AvailabilityZone']
    )

    # 4.Detach current root volume
    print('---Deatch Volume {}'.format(volume_id))
    instance.detach_volume(
        VolumeId=volume_id,
        Device=instance.root_device_name,
    )

    # 5.Attach new volume
    print('---Attach Volume {}'.format(volume_encrypted.id))
    waiter_volume_available.wait(
        VolumeIds=[
            volume_encrypted.id,
        ],
    )

    instance.attach_volume(
        VolumeId=volume_encrypted.id,
        Device=instance.root_device_name
    )

    # 6.Restart instance
    print('---Restart Instance')
    if instance.state['Code'] is 80 or instance.state['Code'] is 64:
        instance.start()

    # Clean up
    snapshot.delete()

    print('Fin')

if __name__ == "__main__":
   main(sys.argv[1:])
